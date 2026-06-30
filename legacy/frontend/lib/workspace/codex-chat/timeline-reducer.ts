'use client';

import type {
  AgentActivityState,
  CodexApprovalRequestCell,
  CodexAssistantCell,
  CodexChatEvent,
  CodexExecCell,
  CodexFileChangeCell,
  CodexReasoningSummaryCell,
  CodexScreenshotCell,
  CodexTimelineProjection,
  CodexTranscriptCell,
  CodexWebSearchCell,
  TimelineProjectionEvent,
  TimelineRow,
} from './cells';
import { projectRawEventToCodexEvents } from './event-projector';

const COMPACT_ACTIVITY_PREFIXES = [
  'running ',
  'completed ',
  'failed ',
  'stopped ',
  'read ',
  'reading ',
  'search ',
  'searched ',
  'searching ',
  'exploring ',
  'list ',
  'listing ',
  'open ',
  'opened ',
  'find ',
  'finding ',
  'edit ',
  'edited ',
  'write ',
  'writing ',
  'wrote ',
  'apply ',
  'applied ',
] as const;

const INTERNAL_THINKING_MARKERS = [
  'state_payload',
  'payload_exceeds',
  'exceeds_default',
  'default_limit',
  'state payload exceeds',
] as const;

function nowIso(): string {
  return new Date().toISOString();
}

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
  const lowered = trimmed.toLowerCase();
  if (INTERNAL_THINKING_MARKERS.some((marker) => lowered.includes(marker))) {
    return '';
  }
  return trimmed;
}

function compactActivityPreview(normalizedText: string): string | null {
  const lines = normalizedText
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return null;
  }
  const activityLines = lines.filter((line) => {
    const lower = line.toLowerCase();
    return COMPACT_ACTIVITY_PREFIXES.some((prefix) => lower.startsWith(prefix));
  });
  if (activityLines.length === lines.length) {
    return activityLines.at(-1) ?? null;
  }
  if (activityLines.length === 1) {
    return activityLines[0] ?? null;
  }
  return null;
}

function mergeThinkingText(existing: string, incoming: string): string {
  const existingTrimmed = existing.trim();
  const incomingTrimmed = incoming.trim();
  if (!incomingTrimmed) {
    return existingTrimmed;
  }
  if (!existingTrimmed) {
    return incomingTrimmed;
  }
  const placeholders = new Set(['thinking...']);
  const existingLower = existingTrimmed.toLowerCase();
  const incomingLower = incomingTrimmed.toLowerCase();
  if (placeholders.has(incomingLower)) {
    return existingTrimmed;
  }
  if (placeholders.has(existingLower)) {
    return incomingTrimmed;
  }
  if (incomingLower === existingLower) {
    return incomingTrimmed;
  }
  if (incomingTrimmed.includes(existingTrimmed)) {
    return incomingTrimmed;
  }
  if (existingTrimmed.includes(incomingTrimmed)) {
    return existingTrimmed;
  }
  return `${existingTrimmed}\n${incomingTrimmed}`;
}

function updateCell<T extends CodexTranscriptCell>(
  cells: CodexTranscriptCell[],
  indexById: Map<string, number>,
  id: string,
  createCell: () => T,
  update: (current: T) => T,
): T {
  const existingIndex = indexById.get(id);
  if (existingIndex === undefined) {
    const cell = createCell();
    indexById.set(id, cells.length);
    cells.push(cell);
    return cell;
  }
  const nextCell = update(cells[existingIndex] as T);
  cells[existingIndex] = nextCell;
  return nextCell;
}

function dimSystemCells(cells: CodexTranscriptCell[]): CodexTranscriptCell[] {
  return cells.map((cell) => {
    if (cell.kind === 'reasoning_summary') {
      return { ...cell, isStreaming: false, dimmed: true };
    }
    if (cell.kind === 'exec' && cell.status === 'running') {
      return { ...cell, status: 'done', dimmed: true };
    }
    if (cell.kind === 'tool' && cell.status === 'running') {
      return { ...cell, status: 'done', dimmed: true };
    }
    if (cell.kind === 'web_search' && cell.status === 'searching') {
      return { ...cell, status: 'done', dimmed: true };
    }
    if (cell.kind === 'file_change' && cell.status === 'running') {
      return { ...cell, status: 'done', dimmed: true };
    }
    if (cell.kind === 'status' && (cell.status === 'idle' || cell.status === 'running')) {
      return { ...cell, status: 'done', dimmed: true };
    }
    if (cell.kind === 'agent_activity' && cell.activity.status === 'active') {
      return {
        ...cell,
        activity: {
          ...cell.activity,
          status: 'completed',
          completedAt: cell.activity.completedAt ?? Date.now(),
        },
        dimmed: true,
      };
    }
    if (
      cell.kind === 'exec'
      || cell.kind === 'tool'
      || cell.kind === 'web_search'
      || cell.kind === 'file_change'
      || cell.kind === 'screenshot'
      || cell.kind === 'artifact'
      || cell.kind === 'status'
      || cell.kind === 'agent_activity'
    ) {
      return { ...cell, dimmed: true };
    }
    return cell;
  });
}

function activityTypeToProjectionState(type: Extract<CodexChatEvent, { type: 'agent_activity' }>['activity']['type']): AgentActivityState {
  if (type === 'thinking') {
    return 'thinking';
  }
  if (type === 'connecting_hardware') {
    return 'connecting';
  }
  if (type === 'running_tool' || type === 'hardware_running') {
    return 'running_tool';
  }
  if (type === 'waiting_approval') {
    return 'waiting';
  }
  if (type === 'finalizing') {
    return 'finalizing';
  }
  if (type === 'error') {
    return 'error';
  }
  if (type === 'done') {
    return 'done';
  }
  return 'idle';
}

function activeStateFromCell(cell: CodexTranscriptCell | null): AgentActivityState {
  if (!cell) {
    return 'idle';
  }
  if (cell.kind === 'agent_activity') {
    if (cell.activity.status === 'failed') {
      return 'error';
    }
    if (cell.activity.status === 'completed') {
      return 'done';
    }
    return activityTypeToProjectionState(cell.activity.type);
  }
  if (cell.kind === 'reasoning_summary' && cell.isStreaming) {
    return 'thinking';
  }
  if (
    (cell.kind === 'exec' || cell.kind === 'tool' || cell.kind === 'file_change')
    && cell.status === 'running'
  ) {
    return 'running_tool';
  }
  if (cell.kind === 'web_search' && cell.status === 'searching') {
    return 'running_tool';
  }
  if (cell.kind === 'status' && (cell.status === 'idle' || cell.status === 'running')) {
    return 'running_tool';
  }
  if (cell.kind === 'approval_request' && cell.status === 'waiting') {
    return 'waiting';
  }
  if (cell.kind === 'assistant' && cell.isStreaming) {
    return 'finalizing';
  }
  return 'idle';
}

function applyCodexEvent(
  cells: CodexTranscriptCell[],
  indexById: Map<string, number>,
  event: CodexChatEvent,
): {
  activeCell: CodexTranscriptCell | null;
  streamStatus: CodexTimelineProjection['streamStatus'];
} {
  if (event.type === 'user') {
    const cell: CodexTranscriptCell = {
      id: event.id,
      kind: 'user',
      content: event.content,
      attachments: event.attachments,
      createdAt: nowIso(),
    };
    cells.push(cell);
    return { activeCell: cell, streamStatus: 'idle' };
  }

  if (event.type === 'reasoning_delta') {
    const cell = updateCell<CodexReasoningSummaryCell>(
      cells,
      indexById,
      'reasoning_summary',
      () => {
        const text = normalizedThinkingContent(event.text);
        return {
          id: 'reasoning_summary',
          kind: 'reasoning_summary',
          text,
          activityLine: compactActivityPreview(text),
          isStreaming: event.isStreaming,
          createdAt: nowIso(),
        };
      },
      (current) => {
        const text = normalizedThinkingContent(mergeThinkingText(current.text, event.text));
        return {
          ...current,
          text,
          activityLine: compactActivityPreview(text),
          isStreaming: event.isStreaming,
        };
      },
    );
    return { activeCell: cell, streamStatus: event.isStreaming ? 'streaming' : 'idle' };
  }

  if (event.type === 'assistant_delta') {
    const cell = updateCell<CodexAssistantCell>(
      cells,
      indexById,
      'assistant',
      () => ({
        id: 'assistant',
        kind: 'assistant',
        content: event.delta,
        isStreaming: true,
        isIncomplete: false,
        effectiveProvider: event.provider,
        effectiveModel: event.model,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        content: `${current.content}${event.delta}`,
        isStreaming: true,
        isIncomplete: false,
        effectiveProvider: event.provider || current.effectiveProvider,
        effectiveModel: event.model || current.effectiveModel,
      }),
    );
    return { activeCell: cell, streamStatus: 'streaming' };
  }

  if (event.type === 'assistant_final') {
    const cell = updateCell<CodexAssistantCell>(
      cells,
      indexById,
      'assistant',
      () => ({
        id: 'assistant',
        kind: 'assistant',
        content: event.content,
        isStreaming: false,
        isIncomplete: event.isIncomplete,
        effectiveProvider: event.provider,
        effectiveModel: event.model,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        content: event.content || current.content,
        isStreaming: false,
        isIncomplete: event.isIncomplete,
        effectiveProvider: event.provider || current.effectiveProvider,
        effectiveModel: event.model || current.effectiveModel,
      }),
    );
    return { activeCell: cell, streamStatus: event.isIncomplete ? 'aborted' : 'complete' };
  }

  if (event.type === 'agent_activity') {
    const activityCellId = `${event.activity.id}:${event.activity.type}`;
    const cell = updateCell(
      cells,
      indexById,
      activityCellId,
      () => ({
        id: activityCellId,
        kind: 'agent_activity' as const,
        activity: event.activity,
        createdAt: new Date(event.activity.startedAt).toISOString(),
      }),
      (current) => ({
        ...current,
        activity: {
          ...current.activity,
          ...event.activity,
          detail: event.activity.detail ?? current.activity.detail,
          startedAt: current.activity.startedAt || event.activity.startedAt,
        },
      }),
    );
    return {
      activeCell: cell,
      streamStatus: event.activity.status === 'failed'
        ? 'error'
        : event.activity.status === 'active'
          ? 'streaming'
          : 'complete',
    };
  }

  if (event.type === 'tool_started') {
    const cell = updateCell(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'tool' as const,
        name: event.name,
        input: event.input ?? null,
        status: 'running' as const,
        result: null,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        name: event.name || current.name,
        input: event.input ?? current.input ?? null,
        status: 'running',
      }),
    );
    return { activeCell: cell, streamStatus: 'streaming' };
  }

  if (event.type === 'tool_result') {
    const cell = updateCell(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'tool' as const,
        name: event.name || 'Tool',
        input: event.input ?? null,
        status: event.status,
        result: event.result,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        name: event.name || current.name,
        input: event.input ?? current.input ?? null,
        status: event.status,
        result: event.result ?? current.result,
      }),
    );
    return { activeCell: cell, streamStatus: event.status === 'error' ? 'error' : 'streaming' };
  }

  if (event.type === 'exec_started') {
    const cell = updateCell<CodexExecCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'exec',
        command: event.command,
        status: 'running',
        output: null,
        exitCode: null,
        runtimeTarget: event.runtimeTarget ?? null,
        machineLabel: event.machineLabel ?? null,
        targetKind: event.targetKind ?? null,
        cwd: event.cwd ?? null,
        durationMs: null,
        stdoutTail: null,
        stderrTail: null,
        approvalStatus: event.approvalStatus ?? null,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        command: event.command || current.command,
        status: 'running',
        runtimeTarget: event.runtimeTarget ?? current.runtimeTarget,
        machineLabel: event.machineLabel ?? current.machineLabel,
        targetKind: event.targetKind ?? current.targetKind,
        cwd: event.cwd ?? current.cwd,
        approvalStatus: event.approvalStatus ?? current.approvalStatus,
      }),
    );
    return { activeCell: cell, streamStatus: 'streaming' };
  }

  if (event.type === 'exec_delta') {
    const cell = updateCell<CodexExecCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'exec',
        command: 'Command',
        status: 'running',
        output: event.output,
        exitCode: null,
        runtimeTarget: null,
        machineLabel: null,
        targetKind: null,
        cwd: null,
        durationMs: null,
        stdoutTail: event.output,
        stderrTail: null,
        approvalStatus: null,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        output: `${current.output ?? ''}${event.output}`,
        stdoutTail: `${current.stdoutTail ?? ''}${event.output}`,
      }),
    );
    return { activeCell: cell, streamStatus: 'streaming' };
  }

  if (event.type === 'exec_result') {
    const cell = updateCell<CodexExecCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'exec',
        command: event.command || 'Command',
        status: event.status,
        output: event.output,
        exitCode: event.exitCode,
        runtimeTarget: event.runtimeTarget ?? null,
        machineLabel: event.machineLabel ?? null,
        targetKind: event.targetKind ?? null,
        cwd: event.cwd ?? null,
        durationMs: event.durationMs ?? null,
        stdoutTail: event.stdoutTail ?? event.output ?? null,
        stderrTail: event.stderrTail ?? null,
        approvalStatus: event.approvalStatus ?? null,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        command: event.command || current.command,
        status: event.status,
        output: event.output ?? current.output,
        exitCode: event.exitCode,
        runtimeTarget: event.runtimeTarget ?? current.runtimeTarget,
        machineLabel: event.machineLabel ?? current.machineLabel,
        targetKind: event.targetKind ?? current.targetKind,
        cwd: event.cwd ?? current.cwd,
        durationMs: event.durationMs ?? current.durationMs,
        stdoutTail: event.stdoutTail ?? event.output ?? current.stdoutTail,
        stderrTail: event.stderrTail ?? current.stderrTail,
        approvalStatus: event.approvalStatus ?? current.approvalStatus,
      }),
    );
    return { activeCell: cell, streamStatus: event.status === 'error' ? 'error' : 'streaming' };
  }

  if (event.type === 'web_search_started') {
    const cell = updateCell<CodexWebSearchCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'web_search',
        query: event.query,
        status: 'searching',
        result: null,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        query: event.query || current.query,
        status: 'searching',
      }),
    );
    return { activeCell: cell, streamStatus: 'streaming' };
  }

  if (event.type === 'web_search_result') {
    const cell = updateCell<CodexWebSearchCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'web_search',
        query: event.query || 'Search',
        status: event.status,
        result: event.result,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        query: event.query || current.query,
        status: event.status,
        result: event.result ?? current.result,
      }),
    );
    return { activeCell: cell, streamStatus: event.status === 'error' ? 'error' : 'streaming' };
  }

  if (event.type === 'file_change') {
    const cell = updateCell<CodexFileChangeCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'file_change',
        filename: event.filename,
        action: event.action,
        status: event.status,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        filename: event.filename || current.filename,
        action: event.action || current.action,
        status: event.status,
      }),
    );
    return { activeCell: cell, streamStatus: event.status === 'error' ? 'error' : 'streaming' };
  }

  if (event.type === 'screenshot_captured') {
    const cell = updateCell<CodexScreenshotCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'screenshot',
        caption: event.caption,
        artifactId: event.artifactId,
        width: event.width,
        height: event.height,
        status: event.status,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        caption: event.caption || current.caption,
        artifactId: event.artifactId || current.artifactId,
        width: event.width ?? current.width,
        height: event.height ?? current.height,
        status: event.status,
      }),
    );
    return { activeCell: cell, streamStatus: event.status === 'error' ? 'error' : 'streaming' };
  }

  if (event.type === 'artifact_created') {
    const cell = updateCell(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'artifact' as const,
        title: event.title,
        artifactId: event.artifactId,
        mimeType: event.mimeType,
        status: event.status,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        title: event.title || current.title,
        artifactId: event.artifactId || current.artifactId,
        mimeType: event.mimeType || current.mimeType,
        status: event.status,
      }),
    );
    return { activeCell: cell, streamStatus: event.status === 'error' ? 'error' : 'streaming' };
  }

  if (event.type === 'approval_request') {
    const approvalStatus = event.status ?? 'waiting';
    const cell = updateCell<CodexApprovalRequestCell>(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'approval_request',
        prompt: event.prompt || 'Approval resolved',
        actions: ['allow_once', 'allow_session', 'deny'],
        status: approvalStatus,
        createdAt: nowIso(),
        metadata: event.metadata,
      }),
      (current) => ({
        ...current,
        prompt: event.prompt || current.prompt,
        status: approvalStatus,
        metadata: {
          ...(current.metadata ?? {}),
          ...(event.metadata ?? {}),
        },
      }),
    );
    return { activeCell: cell, streamStatus: 'streaming' };
  }

  if (event.type === 'status') {
    const cell = updateCell(
      cells,
      indexById,
      event.id,
      () => ({
        id: event.id,
        kind: 'status' as const,
        label: event.label,
        detail: event.detail,
        status: event.status,
        createdAt: nowIso(),
      }),
      (current) => ({
        ...current,
        label: event.label || current.label,
        detail: event.detail ?? current.detail,
        status: event.status,
      }),
    );
    return { activeCell: cell, streamStatus: event.status === 'error' ? 'error' : 'streaming' };
  }

  const cell = updateCell(
    cells,
    indexById,
    event.id,
    () => ({
      id: event.id,
      kind: 'error' as const,
      message: event.message,
      retryable: event.retryable,
      createdAt: nowIso(),
    }),
    (current) => ({
      ...current,
      message: event.message || current.message,
      retryable: event.retryable,
    }),
  );
  return { activeCell: cell, streamStatus: 'error' };
}

function toLegacyTimelineRow(cell: CodexTranscriptCell): TimelineRow | null {
  if (cell.kind === 'user') {
    return { id: cell.id, kind: 'user', content: cell.content };
  }
  if (cell.kind === 'assistant') {
    return {
      id: cell.id,
      kind: 'assistant',
      content: cell.content,
      isStreaming: cell.isStreaming,
      isIncomplete: cell.isIncomplete,
    };
  }
  if (cell.kind === 'reasoning_summary') {
    return {
      id: cell.id,
      kind: 'thinking',
      text: cell.text,
      activityLine: cell.activityLine,
      isStreaming: cell.isStreaming,
    };
  }
  if (cell.kind === 'exec') {
    return {
      id: cell.id,
      kind: 'tool',
      name: cell.command,
      status: cell.status,
      result: cell.output,
    };
  }
  if (cell.kind === 'tool') {
    return {
      id: cell.id,
      kind: 'tool',
      name: cell.name,
      status: cell.status,
      result: cell.result,
    };
  }
  if (cell.kind === 'web_search') {
    return {
      id: cell.id,
      kind: 'search',
      query: cell.query,
      status: cell.status === 'searching' ? 'searching' : 'done',
    };
  }
  if (cell.kind === 'file_change') {
    return {
      id: cell.id,
      kind: 'file',
      filename: cell.filename,
      action: cell.action,
    };
  }
  return null;
}

export function projectCodexTimeline(events: TimelineProjectionEvent[]): CodexTimelineProjection {
  let cells: CodexTranscriptCell[] = [];
  const indexById = new Map<string, number>();
  let activeCell: CodexTranscriptCell | null = null;
  let streamStatus: CodexTimelineProjection['streamStatus'] = 'idle';
  let agentActivityState: AgentActivityState = 'idle';

  for (const [index, event] of events.entries()) {
    const projectedEvents = projectRawEventToCodexEvents(event, index);
    for (const projectedEvent of projectedEvents) {
      const result = applyCodexEvent(cells, indexById, projectedEvent);
      activeCell = result.activeCell;
      streamStatus = result.streamStatus;
      agentActivityState = activeStateFromCell(activeCell);
      if (projectedEvent.type === 'assistant_final') {
        cells = dimSystemCells(cells);
        agentActivityState = activeStateFromCell(activeCell);
      }
    }
  }

  const approvalQueue = cells.filter((cell): cell is CodexApprovalRequestCell =>
    cell.kind === 'approval_request' && cell.status === 'waiting',
  );

  return {
    committedCells: cells,
    activeCell,
    activeTurnId: null,
    streamStatus,
    agentActivityState,
    approvalQueue,
    composerState: {
      draft: '',
      canSend: streamStatus !== 'streaming',
      canStop: streamStatus === 'streaming',
    },
    cells,
  };
}

export function projectTimeline(events: TimelineProjectionEvent[]): TimelineRow[] {
  return projectCodexTimeline(events).cells
    .map(toLegacyTimelineRow)
    .filter((row): row is TimelineRow => row !== null);
}

export type {
  TimelineProjectionEvent,
  TimelineRow,
};
