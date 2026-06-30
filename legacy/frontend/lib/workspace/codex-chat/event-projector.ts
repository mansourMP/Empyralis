'use client';

import type {
  AgentActivityEvent,
  CodexChatEvent,
  TimelineProjectionEvent,
} from './cells';

const AGENT_COMPUTER_CONNECTING_LABEL = 'Connecting to your Mac...';
const AGENT_COMPUTER_RUNNING_LABEL = 'Running on your Mac';
const AGENT_COMPUTER_DONE_LABEL = 'Done';
const AGENT_COMPUTER_REQUIRED_LABEL = 'Needs Agent Computer';
const AGENT_COMPUTER_OFFLINE_LABEL = 'Agent Computer offline';
function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function readObject(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function readTimestampMs(...values: unknown[]): number {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
    const text = readString(value);
    if (!text) {
      continue;
    }
    const numeric = Number(text);
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric;
    }
    const parsed = Date.parse(text);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return Date.now();
}

function safeActivityDetail(...values: unknown[]): string | undefined {
  for (const value of values) {
    const text = readString(value).replace(/\s+/g, ' ').trim();
    if (!text) {
      continue;
    }
    if (/<\s*(?:[|｜]\s*[|｜])|\bDSML\b|tool_calls|<\/?\s*xml/i.test(text)) {
      continue;
    }
    if (text.startsWith('{') || text.startsWith('[')) {
      continue;
    }
    return text.slice(0, 500);
  }
  return undefined;
}

function safeActivityLabel(value: unknown, fallback: string): string {
  const text = readString(value).replace(/\s+/g, ' ').trim();
  if (!text) {
    return fallback;
  }
  if (
    /<\s*(?:[|｜]\s*[|｜])|\bDSML\b|tool_calls|tool\.invoke|gateway_id|capability_id|runtime_session_id|local_gateway|user_device_gateway/i.test(text)
    || /^gateway[-_:]/i.test(text)
    || /^gw[-_:]/i.test(text)
    || /gateway offline/i.test(text)
    || /mac is not connected/i.test(text)
  ) {
    return fallback;
  }
  return text.slice(0, 120);
}

function normalizeStepStatus(value: unknown): 'running' | 'done' | 'error' {
  const normalized = readString(value).toLowerCase();
  if (normalized === 'done' || normalized === 'complete' || normalized === 'completed' || normalized === 'success') {
    return 'done';
  }
  if (normalized === 'error' || normalized === 'failed' || normalized === 'failure') {
    return 'error';
  }
  return 'running';
}

function normalizeToolResultStatus(value: unknown): 'running' | 'done' | 'error' {
  const normalized = readString(value).toLowerCase();
  if (['done', 'complete', 'completed', 'success', 'succeeded'].includes(normalized)) {
    return 'done';
  }
  if (['running', 'queued', 'waiting', 'waiting_approval', 'waiting-for-approval', 'pending'].includes(normalized)) {
    return 'running';
  }
  if (['error', 'failed', 'failure', 'blocked', 'denied', 'offline', 'degraded'].includes(normalized)) {
    return 'error';
  }
  return 'done';
}

function normalizeAgentActivityStatus(value: unknown): AgentActivityEvent['status'] {
  const normalized = readString(value).toLowerCase();
  if (['completed', 'complete', 'done', 'success', 'succeeded', 'ready'].includes(normalized)) {
    return 'completed';
  }
  if (['failed', 'failure', 'error', 'offline', 'degraded', 'blocked', 'denied', 'terminated'].includes(normalized)) {
    return 'failed';
  }
  return 'active';
}

function activityLabelForHardwareState(state: string): Pick<AgentActivityEvent, 'type' | 'label' | 'status'> {
  if (['queued', 'connecting', 'starting', 'started', 'pending'].includes(state)) {
    return { type: 'connecting_hardware', label: AGENT_COMPUTER_CONNECTING_LABEL, status: 'active' };
  }
  if (state === 'running') {
    return { type: 'hardware_running', label: AGENT_COMPUTER_RUNNING_LABEL, status: 'active' };
  }
  if (['completed', 'complete', 'done', 'ready', 'success', 'succeeded', 'terminated', 'stopped', 'cancelled', 'canceled'].includes(state)) {
    return { type: 'done', label: AGENT_COMPUTER_DONE_LABEL, status: 'completed' };
  }
  if (state === 'waiting_approval' || state === 'waiting' || state === 'approval_required') {
    return { type: 'waiting_approval', label: AGENT_COMPUTER_APPROVAL_LABEL, status: 'active' };
  }
  if (state === 'requires_agent_computer' || state === 'needs_agent_computer' || state === 'required') {
    return { type: 'error', label: AGENT_COMPUTER_REQUIRED_LABEL, status: 'failed' };
  }
  if (state === 'offline' || state === 'degraded') {
    return { type: 'error', label: AGENT_COMPUTER_OFFLINE_LABEL, status: 'failed' };
  }
  if (['failed', 'failure', 'error', 'blocked', 'denied'].includes(state)) {
    return { type: 'error', label: AGENT_COMPUTER_OFFLINE_LABEL, status: 'failed' };
  }
  return { type: 'hardware_running', label: AGENT_COMPUTER_RUNNING_LABEL, status: 'active' };
}

function agentActivityFromObject(
  rawActivity: unknown,
  fallback: Pick<AgentActivityEvent, 'id' | 'type' | 'label' | 'startedAt' | 'status'>,
): AgentActivityEvent {
  const activity = readObject(rawActivity);
  const completedAt = readTimestampMs(activity.completedAt, activity.completed_at);
  const hasCompletedAt = Boolean(readString(activity.completedAt) || readString(activity.completed_at) || readNumber(activity.completedAt) || readNumber(activity.completed_at));
  const rawType = readString(activity.type);
  const rawStatus = readString(activity.status);
  return {
    id: readString(activity.id) || fallback.id,
    type: rawType ? rawType as AgentActivityEvent['type'] : fallback.type,
    label: safeActivityLabel(activity.label, fallback.label),
    detail: safeActivityDetail(activity.detail, activity.summary),
    startedAt: readTimestampMs(activity.startedAt, activity.started_at, fallback.startedAt),
    ...(hasCompletedAt ? { completedAt } : {}),
    status: rawStatus ? normalizeAgentActivityStatus(rawStatus) : fallback.status,
  };
}

function hardwareActivityFromTrace(
  payload: Record<string, unknown>,
  data: Record<string, unknown>,
  fallbackIndex: number,
  explicitState?: string,
): AgentActivityEvent {
  const state = (explicitState || readString(data.state || data.status) || 'running').toLowerCase();
  const base = activityLabelForHardwareState(state);
  const id = eventId('agent-activity', data.runtime_session_id || payload.tool_call_id || payload.item_id || data.request_id || data.action_id, fallbackIndex);
  const startedAt = readTimestampMs(data.started_at, data.startedAt, payload.ts);
  return agentActivityFromObject(data.agent_activity, {
    id,
    ...base,
    startedAt,
    status: base.status,
  });
}

function normalizeApprovalStatus(eventType: string, data: Record<string, unknown>): 'waiting' | 'done' | 'error' {
  const token = readString(data.decision || data.resolution || data.status).toLowerCase();
  if (['rejected', 'reject', 'denied', 'deny', 'no', 'cancel', 'cancelled', 'canceled', 'stopped', 'aborted', 'failed', 'error'].includes(token)) {
    return 'error';
  }
  if (
    eventType.includes('resolved')
    || ['approved', 'approve', 'allowed', 'allow_once', 'allow_session', 'accepted', 'proceed', 'executed', 'done', 'resolved'].includes(token)
  ) {
    return 'done';
  }
  return 'waiting';
}

function fileActionLabel(label: string, fallback: string): string {
  const lower = `${label} ${fallback}`.toLowerCase();
  if (lower.includes('read')) {
    return 'Read';
  }
  if (lower.includes('rename') || lower.includes('move')) {
    return 'Move';
  }
  if (lower.includes('write')) {
    return 'Write';
  }
  if (lower.includes('open')) {
    return 'Open';
  }
  if (lower.includes('delete')) {
    return 'Delete';
  }
  return label || fallback || 'Updated';
}

function isSendish(value: string): boolean {
  return /send|sent|dispatch|deliver|reply|post|submit|outbound/i.test(value);
}

function isSearchLabel(value: string): boolean {
  return /search|searched|web/i.test(value);
}

function isFileLabel(value: string): boolean {
  return /file|read|write|open|path|desktop|folder/i.test(value);
}

function isShellLabel(value: string): boolean {
  return /shell|command|terminal|bash|zsh|exec|run/i.test(value);
}

function isShellToolName(toolName: string, command: string): boolean {
  if (command.trim()) {
    return true;
  }
  return /shell|command|terminal|bash|zsh|exec/i.test(toolName);
}

function toolInputRecord(data: Record<string, unknown>): Record<string, unknown> {
  const candidates = [
    data.input,
    data.arguments,
    data.args_preview,
    data.args,
    data.parameters,
  ];
  for (const candidate of candidates) {
    const record = readObject(candidate);
    if (Object.keys(record).length > 0) {
      return record;
    }
  }
  return {};
}

function compactJsonPreview(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'string') {
    return value.trim().slice(0, 1000);
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2).slice(0, 1000);
  } catch {
    return '';
  }
}

function toolInputPreview(data: Record<string, unknown>): string | null {
  const command = shellCommandFromData(data);
  if (command) {
    return command;
  }
  const input = toolInputRecord(data);
  const query = readString(data.query) || readString(input.query);
  if (query) {
    return query;
  }
  const url = readString(data.url) || readString(input.url);
  if (url) {
    return url;
  }
  const path = filePathFromData(data);
  if (path) {
    return path;
  }
  const directInput = compactJsonPreview(data.input ?? data.arguments ?? data.args ?? data.parameters);
  if (directInput) {
    return directInput;
  }
  const summary = readString(data.summary) || readString(data.detail);
  return summary || null;
}

function toolResultText(data: Record<string, unknown>): string | null {
  return readString(data.summary)
    || readString(data.result)
    || readString(data.output)
    || readString(data.text)
    || null;
}

const EXEC_OUTPUT_MAX_LINES = 30;
const EXEC_OUTPUT_MAX_CHARS = 8192;

function outputTail(...values: unknown[]): string | null {
  for (const value of values) {
    const text = readString(value);
    if (!text) {
      continue;
    }
    const charTail = text.length > EXEC_OUTPUT_MAX_CHARS ? text.slice(-EXEC_OUTPUT_MAX_CHARS) : text;
    const lines = charTail.replace(/\r/g, '').split('\n');
    return lines.length > EXEC_OUTPUT_MAX_LINES
      ? lines.slice(-EXEC_OUTPUT_MAX_LINES).join('\n')
      : charTail;
  }
  return null;
}

function toolDisplayName(rawName: string): string {
  const normalized = rawName
    .replace(/[_:.-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const lower = normalized.toLowerCase();
  if (!lower) {
    return 'Using tool';
  }
  if (lower.includes('telegram')) {
    return isSendish(lower) ? 'Sending Telegram' : 'Using Telegram';
  }
  if (lower.includes('whatsapp')) {
    return isSendish(lower) ? 'Sending WhatsApp' : 'Using WhatsApp';
  }
  if (lower.includes('gmail') || lower.includes('email') || lower.includes('mail')) {
    return isSendish(lower) ? 'Sending email' : 'Using email';
  }
  if (lower.includes('image')) {
    return 'Generating image';
  }
  if (lower.includes('browser')) {
    return 'Browser action';
  }
  return normalized.replace(/\b\w/g, (char) => char.toUpperCase());
}

function specialistDisplayName(data: Record<string, unknown>): string {
  return readString(data.specialist_name)
    || readString(data.name)
    || readString(data.label)
    || 'Specialist';
}

function browserActionDisplay(action: string): string {
  const normalized = action.toLowerCase();
  if (!normalized) {
    return 'Browser action';
  }
  return 'Browser action';
}

function traceStatus(value: unknown): 'running' | 'done' | 'error' {
  return normalizeToolResultStatus(value);
}

function statusFromEventType(eventType: string, explicitStatus: unknown): 'running' | 'done' | 'error' {
  const normalized = readString(explicitStatus).toLowerCase();
  if (normalized) {
    return traceStatus(normalized);
  }
  if (/started|queued|waiting|pending|running/.test(eventType)) {
    return 'running';
  }
  if (/failed|failure|error|blocked|denied|offline|degraded/.test(eventType)) {
    return 'error';
  }
  return 'done';
}

function shellCommandFromData(data: Record<string, unknown>): string {
  const input = toolInputRecord(data);
  return readString(data.command)
    || readString(data.cmd)
    || readString(input.command)
    || readString(input.cmd);
}

function execMetaFromData(
  data: Record<string, unknown>,
  metadata: Record<string, unknown> = {},
): Pick<
  Extract<CodexChatEvent, { type: 'exec_result' }>,
  'runtimeTarget' | 'machineLabel' | 'targetKind' | 'cwd' | 'durationMs' | 'stdoutTail' | 'stderrTail' | 'approvalStatus'
> {
  const input = toolInputRecord(data);
  return {
    runtimeTarget: readString(data.runtime_target) || readString(metadata.runtime_target) || null,
    machineLabel: readString(data.machine_label)
      || readString(data.target_summary)
      || readString(metadata.label)
      || null,
    targetKind: readString(data.target_kind) || readString(data.runtime_access_mode) || null,
    cwd: readString(data.cwd)
      || readString(data.working_directory)
      || readString(input.cwd)
      || readString(input.workingDir)
      || null,
    durationMs: readNumber(data.duration_ms ?? data.durationMs),
    stdoutTail: outputTail(
      data.stdout_tail,
      data.stdout,
      data.output_tail,
      data.output_preview,
      data.stdout_preview,
      data.summary,
    ),
    stderrTail: outputTail(data.stderr_tail, data.stderr, data.stderr_preview, data.error),
    approvalStatus: readString(data.approval_status)
      || readString(data.approvalStatus)
      || readString(data.decision)
      || null,
  };
}

function filePathFromData(data: Record<string, unknown>): string {
  const input = toolInputRecord(data);
  return readString(data.path)
    || readString(data.file_path)
    || readString(data.filename)
    || readString(input.path)
    || readString(input.file_path)
    || readString(input.filename)
    || readString(input.name);
}

function fileActionFromEventType(eventType: string, fallback: string): string {
  if (eventType.includes('delete') || eventType.includes('remove')) {
    return 'Delete';
  }
  if (eventType.includes('write') || eventType.includes('create') || eventType.includes('update') || eventType.includes('change')) {
    return 'Write';
  }
  if (eventType.includes('rename') || eventType.includes('move')) {
    return 'Move';
  }
  if (eventType.includes('open')) {
    return 'Open';
  }
  return fileActionLabel(fallback, 'Read');
}

function runtimeStatusLabel(eventType: string, data: Record<string, unknown>): string {
  const state = readString(data.state || data.status).toLowerCase();
  if (state === 'requires_agent_computer' || state === 'needs_agent_computer' || state === 'required') {
    return AGENT_COMPUTER_REQUIRED_LABEL;
  }
  if (state === 'offline' || eventType.includes('offline')) {
    return AGENT_COMPUTER_OFFLINE_LABEL;
  }
  if (state === 'degraded' || eventType.includes('degraded')) {
    return AGENT_COMPUTER_OFFLINE_LABEL;
  }
  if (eventType.includes('blocked')) {
    return 'Action blocked';
  }
  if (eventType.includes('terminated') || eventType.includes('cancel') || eventType.includes('stop')) {
    return 'Stopped';
  }
  return readString(data.label) || readString(data.title) || 'Runtime status';
}

function eventId(prefix: string, candidate: unknown, fallbackIndex: number): string {
  return readString(candidate) || `${prefix}:${fallbackIndex}`;
}

function projectStepEvent(payload: Record<string, unknown>, fallbackIndex: number): CodexChatEvent[] {
  const kind = readString(payload.kind).toLowerCase();
  const id = readString(payload.id) || `${kind || 'step'}:${fallbackIndex}`;
  const label = readString(payload.label);
  const detail = readString(payload.detail);
  const status = normalizeStepStatus(payload.status);
  const combined = `${label} ${detail}`.trim();

  if (kind === 'agent_activity') {
    const state = readString(payload.activity_state || payload.state || payload.status).toLowerCase();
    const base = state ? activityLabelForHardwareState(state) : {
      type: 'running_tool' as const,
      label: label || 'Working on this',
      status: normalizeStepStatus(payload.status) === 'error'
        ? 'failed' as const
        : normalizeStepStatus(payload.status) === 'done'
          ? 'completed' as const
          : 'active' as const,
    };
    return [{
      type: 'agent_activity',
      activity: agentActivityFromObject(payload.activity, {
        id,
        ...base,
        label: label || base.label,
        startedAt: readTimestampMs(payload.startedAt, payload.started_at),
      }),
    }];
  }

  if (kind === 'thinking') {
    return [{
      type: 'reasoning_delta',
      id: 'reasoning_summary',
      text: detail || label || 'Thinking',
      isStreaming: status === 'running',
    }];
  }

  if (kind === 'browser') {
    if (status === 'running') {
      return [{ type: 'tool_started', id, name: label || 'Using browser' }];
    }
    return [{
      type: 'tool_result',
      id,
      name: label || 'Using browser',
      status: status === 'error' ? 'error' : 'done',
      result: detail || null,
    }];
  }

  if (kind === 'shell' || isShellLabel(combined)) {
    const metadata = readObject(payload.metadata);
    const execMeta = execMetaFromData(payload, metadata);
    const command = readString(payload.command) || detail || label || 'Running command';
    if (status === 'running') {
      return [{
        type: 'exec_started',
        id,
        command,
        runtimeTarget: execMeta.runtimeTarget,
        machineLabel: execMeta.machineLabel,
        targetKind: execMeta.targetKind,
        cwd: execMeta.cwd,
        approvalStatus: execMeta.approvalStatus,
      }];
    }
    return [{
      type: 'exec_result',
      id,
      command,
      status,
      output: detail || execMeta.stdoutTail || null,
      exitCode: readNumber(payload.exit_code),
      ...execMeta,
    }];
  }

  if (kind === 'file' || isFileLabel(combined)) {
    return [{
      type: 'file_change',
      id,
      filename: detail || label || 'File',
      action: fileActionLabel(label, detail),
      status,
    }];
  }

  if (kind === 'search' || isSearchLabel(combined)) {
    if (status === 'running') {
      return [{ type: 'web_search_started', id, query: detail || label || 'Search' }];
    }
    return [{ type: 'web_search_result', id, query: detail || label || null, status: status === 'error' ? 'error' : 'done', result: detail || null }];
  }

  if (status === 'running') {
    return [{ type: 'tool_started', id, name: label || detail || 'Tool' }];
  }
  return [{
    type: 'tool_result',
    id,
    name: label || null,
    status: status === 'error' ? 'error' : 'done',
    result: detail || null,
  }];
}

function projectTraceEvent(payload: Record<string, unknown>, fallbackIndex: number): CodexChatEvent[] {
  const eventType = readString(payload.event_type).toLowerCase();
  const data = readObject(payload.data);
  const metadata = readObject(payload.metadata);

  if (eventType === 'trace.started') {
    return [];
  }

  if (eventType === 'reasoning.summary.delta') {
    const delta = readString(data.delta);
    return delta
      ? [{ type: 'reasoning_delta', id: 'reasoning_summary', text: delta, isStreaming: true }]
      : [];
  }

  if (eventType === 'assistant.message.delta') {
    // The transport already emits assistant text as chunk events. Trace deltas
    // are transparency metadata, not a second source of transcript text.
    return [];
  }

  if (eventType === 'tool.started') {
    const toolName = readString(data.tool_name) || readString(data.capability_id) || readString(data.name) || 'Tool';
    const input = toolInputRecord(data);
    const command = shellCommandFromData(data);
    const query = readString(data.query) || readString(input.query);
    const path = filePathFromData(data);
    const id = eventId('tool', payload.tool_call_id || payload.item_id, fallbackIndex);
    const combined = `${toolName} ${command} ${query} ${path}`;
    const connectorId = readString(data.connector_id).toLowerCase();
    if (connectorId === 'hardware_runtime') {
      return [{
        type: 'agent_activity',
        activity: hardwareActivityFromTrace(payload, data, fallbackIndex, 'connecting'),
      }];
    }
    if (isShellToolName(toolName, command)) {
      const execMeta = execMetaFromData(data, metadata);
      return [{
        type: 'exec_started',
        id,
        command: command || 'Running shell',
        runtimeTarget: execMeta.runtimeTarget,
        machineLabel: execMeta.machineLabel,
        targetKind: execMeta.targetKind,
        cwd: execMeta.cwd,
        approvalStatus: execMeta.approvalStatus,
      }];
    }
    if (isFileLabel(combined)) {
      return [{
        type: 'file_change',
        id,
        filename: path || readString(input.name) || 'File',
        action: fileActionLabel(toolName, path || 'Reading'),
        status: 'running',
      }];
    }
    if (isSearchLabel(combined)) {
      return [{
        type: 'web_search_started',
        id,
        query: query || 'Searching web',
      }];
    }
    return [{
      type: 'tool_started',
      id,
      name: toolDisplayName(toolName),
      input: toolInputPreview(data),
    }];
  }

  if (eventType === 'tool.result') {
    const status = normalizeToolResultStatus(data.status);
    const toolName = readString(data.tool_name) || readString(data.capability_id) || readString(data.name) || 'Tool';
    const input = toolInputRecord(data);
    const command = shellCommandFromData(data);
    const query = readString(data.query) || readString(input.query);
    const path = filePathFromData(data);
    const result = toolResultText(data);
    const id = eventId('tool', payload.tool_call_id || payload.item_id, fallbackIndex);
    const combined = `${toolName} ${command} ${query} ${path}`;
    const artifactIds = Array.isArray(data.artifact_ids) ? data.artifact_ids : [];
    const artifactId = readString(artifactIds[0]);
    const connectorId = readString(data.connector_id).toLowerCase();
    if (connectorId === 'hardware_runtime' || readObject(data.agent_activity).id) {
      const activityEvent: CodexChatEvent = {
        type: 'agent_activity',
        activity: hardwareActivityFromTrace(payload, data, fallbackIndex),
      };
      if (combined.toLowerCase().includes('screenshot') && artifactId) {
        return [
          activityEvent,
          {
            type: 'screenshot_captured',
            id: eventId('artifact', artifactId, fallbackIndex),
            caption: result || 'Screenshot captured',
            artifactId,
            width: readNumber(data.width),
            height: readNumber(data.height),
            status: status === 'error' ? 'error' : 'done',
          },
        ];
      }
      return [activityEvent];
    }
    if (combined.toLowerCase().includes('screenshot') && artifactId) {
      return [{
        type: 'screenshot_captured',
        id: eventId('artifact', artifactId, fallbackIndex),
        caption: result || 'Screenshot captured',
        artifactId,
        width: readNumber(data.width),
        height: readNumber(data.height),
        status: status === 'error' ? 'error' : 'done',
      }];
    }
    if (isShellToolName(toolName, command)) {
      const execMeta = execMetaFromData(data, metadata);
      if (status === 'running') {
        return [{
          type: 'exec_started',
          id,
          command: result || command || 'Running shell',
          runtimeTarget: execMeta.runtimeTarget,
          machineLabel: execMeta.machineLabel,
          targetKind: execMeta.targetKind,
          cwd: execMeta.cwd,
          approvalStatus: execMeta.approvalStatus,
        }];
      }
      return [{
        type: 'exec_result',
        id,
        command: command || undefined,
        status,
        output: execMeta.stdoutTail ?? result,
        exitCode: readNumber(data.exit_code),
        ...execMeta,
      }];
    }
    if (isFileLabel(combined)) {
      return [{
        type: 'file_change',
        id,
        filename: path || readString(input.name) || 'File',
        action: fileActionLabel(toolName, path || 'Read'),
        status,
      }];
    }
    if (isSearchLabel(combined)) {
      if (status === 'running') {
        return [{
          type: 'web_search_started',
          id,
          query: query || result || 'Searching web',
        }];
      }
      return [{
        type: 'web_search_result',
        id,
        query: query || null,
        status,
        result,
      }];
    }
    return [{
      type: 'tool_result',
      id,
      name: toolDisplayName(toolName),
      input: toolInputPreview(data),
      status,
      result,
    }];
  }

  if (eventType === 'delegation.started') {
    const specialistName = specialistDisplayName(data);
    return [{
      type: 'tool_result',
      id: eventId('delegation-started', `${readString(payload.item_id) || readString(data.action_id) || specialistName}:started`, fallbackIndex),
      name: `Delegated to ${specialistName}`,
      status: 'done',
      result: readString(data.task_summary) || readString(data.summary) || null,
    }];
  }

  if (eventType === 'delegation.finished') {
    const status = normalizeToolResultStatus(data.status);
    const result = readString(data.result_summary)
      || readString(data.summary)
      || readString(data.detail)
      || null;
    return [{
      type: 'tool_result',
      id: eventId('delegation-finished', `${readString(payload.item_id) || readString(data.action_id) || readString(data.name) || 'specialist'}:finished`, fallbackIndex),
      name: 'Specialist finished',
      status,
      result,
    }];
  }

  if (eventType.startsWith('shell.')) {
    const status = statusFromEventType(eventType, data.status || data.state);
    const id = eventId('shell', payload.tool_call_id || payload.item_id || data.action_id, fallbackIndex);
    const command = shellCommandFromData(data) || readString(data.detail) || 'Running shell';
    const execMeta = execMetaFromData(data, metadata);
    if (status === 'running') {
      return [{
        type: 'exec_started',
        id,
        command,
        runtimeTarget: execMeta.runtimeTarget,
        machineLabel: execMeta.machineLabel,
        targetKind: execMeta.targetKind,
        cwd: execMeta.cwd,
        approvalStatus: execMeta.approvalStatus,
      }];
    }
    return [{
      type: 'exec_result',
      id,
      command,
      status: status === 'error' ? 'error' : 'done',
      output: execMeta.stdoutTail ?? toolResultText(data),
      exitCode: readNumber(data.exit_code),
      ...execMeta,
    }];
  }

  if (eventType.startsWith('file.')) {
    const status = statusFromEventType(eventType, data.status || data.state);
    const path = filePathFromData(data);
    return [{
      type: 'file_change',
      id: eventId('file', payload.item_id || payload.tool_call_id || data.action_id || path, fallbackIndex),
      filename: path || readString(data.summary) || 'File',
      action: fileActionFromEventType(eventType, readString(data.action) || readString(data.label)),
      status,
    }];
  }

  if (eventType === 'browser.action' || eventType === 'computer.browser.action') {
    const action = readString(data.action) || readString(data.kind) || readString(data.event) || 'Action';
    const summary = readString(data.summary) || readString(data.detail) || null;
    return [{
      type: 'tool_result',
      id: eventId('browser-action', payload.item_id || payload.tool_call_id, fallbackIndex),
      name: browserActionDisplay(action),
      status: readString(data.status).toLowerCase() === 'error' ? 'error' : 'done',
      result: summary,
    }];
  }

  if (eventType.startsWith('runtime.') || eventType.startsWith('gateway.') || eventType.startsWith('hardware.')) {
    const status = statusFromEventType(eventType, data.status || data.state);
    const action = readString(data.action) || readString(data.capability_id) || readString(data.tool_name);
    if (readObject(data.agent_activity).id || readString(data.runtime_session_id) || readString(data.state || data.status)) {
      return [{
        type: 'agent_activity',
        activity: hardwareActivityFromTrace(payload, data, fallbackIndex),
      }];
    }
    if (action && !eventType.includes('status') && !eventType.includes('session')) {
      return [{
        type: 'tool_result',
        id: eventId('hardware', payload.item_id || payload.tool_call_id || data.action_id, fallbackIndex),
        name: toolDisplayName(action),
        status,
        result: readString(data.summary) || readString(data.detail) || null,
      }];
    }
    return [{
      type: 'status',
      id: eventId('runtime', payload.item_id || data.action_id || data.runtime_session_id, fallbackIndex),
      label: runtimeStatusLabel(eventType, data),
      detail: readString(data.summary) || readString(data.detail) || null,
      status: status === 'error' ? 'error' : status === 'running' ? 'running' : 'done',
    }];
  }

  if (eventType.includes('telegram')) {
    const status = readString(data.status).toLowerCase() === 'error' ? 'error' : 'done';
    const channelSummary = `${eventType} ${readString(data.delivery_transport)} ${readString(data.summary)} ${readString(data.message)}`;
    return [{
      type: 'tool_result',
      id: eventId('telegram', payload.item_id || payload.tool_call_id, fallbackIndex),
      name: isSendish(channelSummary) ? 'Sending Telegram' : 'Using Telegram',
      status,
      result: readString(data.summary) || readString(data.message) || null,
    }];
  }

  if (eventType.includes('whatsapp')) {
    const status = readString(data.status).toLowerCase() === 'error' ? 'error' : 'done';
    const channelSummary = `${eventType} ${readString(data.delivery_transport)} ${readString(data.summary)} ${readString(data.message)}`;
    return [{
      type: 'tool_result',
      id: eventId('whatsapp', payload.item_id || payload.tool_call_id, fallbackIndex),
      name: isSendish(channelSummary) ? 'Sending WhatsApp' : 'Using WhatsApp',
      status,
      result: readString(data.summary) || readString(data.message) || null,
    }];
  }

  if (eventType === 'search.query') {
    return [{
      type: 'web_search_started',
      id: eventId('search', payload.tool_call_id || payload.item_id, fallbackIndex),
      query: readString(data.query) || 'Search',
    }];
  }

  if (eventType === 'search.results') {
    return [{
      type: 'web_search_result',
      id: eventId('search', payload.tool_call_id || payload.item_id, fallbackIndex),
      query: readString(data.query) || null,
      status: 'done',
      result: readString(data.summary) || null,
    }];
  }

  if (eventType === 'browser.screenshot' || eventType === 'screenshot.captured') {
    const artifactId = readString(data.artifact_id)
      || readString(data.artifactId)
      || readString(metadata.artifact_id)
      || null;
    const caption = readString(data.caption)
      || readString(data.description)
      || readString(data.summary)
      || 'Screenshot captured';
    return [{
      type: 'screenshot_captured',
      id: eventId('screenshot', artifactId || payload.item_id || payload.tool_call_id, fallbackIndex),
      caption,
      artifactId,
      width: readNumber(data.width),
      height: readNumber(data.height),
      status: readString(data.status).toLowerCase() === 'error' ? 'error' : 'done',
    }];
  }

  if (eventType === 'artifact.created') {
    const mimeType = readString(data.mime_type) || readString(data.mimeType);
    const artifactKind = readString(data.kind).toLowerCase();
    const artifactTitle = `${readString(data.title)} ${readString(data.label)}`.toLowerCase();
    if (mimeType.startsWith('image/') || artifactKind.includes('screenshot') || artifactTitle.includes('screenshot')) {
      const artifactId = readString(data.artifact_id)
        || readString(data.artifactId)
        || readString(payload.item_id)
        || null;
      return [{
        type: 'screenshot_captured',
        id: eventId('artifact', artifactId || payload.item_id, fallbackIndex),
        caption: readString(data.title) || readString(data.label) || 'Screenshot captured',
        artifactId,
        width: readNumber(data.width),
        height: readNumber(data.height),
        status: 'done',
      }];
    }
    const artifactId = readString(data.artifact_id)
      || readString(data.artifactId)
      || readString(payload.artifact_id)
      || readString(payload.item_id)
      || null;
    return [{
      type: 'artifact_created',
      id: eventId('artifact', artifactId || payload.item_id, fallbackIndex),
      title: readString(data.title) || readString(data.label) || readString(data.mime_type) || 'Artifact created',
      artifactId,
      mimeType: mimeType || null,
      status: readString(data.status).toLowerCase() === 'error' ? 'error' : 'done',
    }];
  }

  if (eventType === 'plan.item.created') {
    const title = readString(data.title);
    const summary = readString(data.rationale_summary);
    const combined = `${title} ${summary}`.toLowerCase();
    if (combined.includes('search')) {
      return [{
        type: 'web_search_started',
        id: eventId('search-plan', data.item_id, fallbackIndex),
        query: title || 'Search',
      }];
    }
    if (combined.includes('file') || combined.includes('read') || combined.includes('open')) {
      return [{
        type: 'file_change',
        id: eventId('file-plan', data.item_id, fallbackIndex),
        filename: title || 'File',
        action: 'Read',
        status: 'done',
      }];
    }
  }

  if (eventType.includes('approval')) {
    const normalizedApproval = data.normalized_approval && typeof data.normalized_approval === 'object'
      ? data.normalized_approval as Record<string, unknown>
      : {};
    const approvalId = data.approval_id
      || payload.approval_id
      || data.id
      || normalizedApproval.approval_id
      || normalizedApproval.id
      || payload.item_id;
    const status = normalizeApprovalStatus(eventType, {
      ...normalizedApproval,
      ...data,
    });
    return [{
      type: 'approval_request',
      id: eventId('approval', approvalId, fallbackIndex),
      prompt: readString(data.prompt)
        || readString(data.message)
        || readString(data.title)
        || readString(data.description)
        || readString(normalizedApproval.action)
        || (status === 'waiting' ? 'Approval required' : ''),
      status,
      metadata: {
        ...data,
        ...normalizedApproval,
        approval_id: readString(data.approval_id)
          || readString(payload.approval_id)
          || readString(normalizedApproval.approval_id)
          || readString(normalizedApproval.id),
        code: readString(data.code) || readString(normalizedApproval.code),
      },
    }];
  }

  if (
    eventType === 'trace.completed'
    || eventType === 'run.completed'
    || eventType === 'turn.completed'
    || eventType === 'assistant.completed'
  ) {
    const completionDetail = readString(data.summary) || readString(data.outcome) || readString(data.result) || null;
    return [{
      type: 'status',
      id: eventId('done', payload.trace_id || payload.item_id, fallbackIndex),
      label: 'Done',
      detail: completionDetail,
      status: 'done',
    }];
  }

  return [];
}

function projectTypedStreamEvent(payload: Record<string, unknown>, fallbackIndex: number): CodexChatEvent[] {
  const rawType = readString(payload.event_type || payload.type || payload.event || payload.kind).toLowerCase();
  const data = readObject(payload.payload);
  const merged = Object.keys(data).length > 0 ? { ...payload, ...data } : payload;
  const id = eventId('typed', merged.tool_call_id || merged.id || merged.item_id, fallbackIndex);
  if (rawType === 'thinking') {
    const text = readString(merged.delta)
      || readString(merged.text)
      || readString(merged.content)
      || readString(merged.message);
    return text
      ? [{
          type: 'reasoning_delta',
          id: 'reasoning_summary',
          text,
          isStreaming: readString(merged.status).toLowerCase() !== 'done' && merged.done !== true,
        }]
      : [];
  }
  if (rawType === 'tool_call') {
    const name = readString(merged.tool_name) || readString(merged.name) || readString(merged.tool) || 'Tool';
    const command = shellCommandFromData(merged);
    const input = toolInputPreview(merged);
    if (isShellToolName(name, command)) {
      return [{
        type: 'exec_started',
        id,
        command: command || input || 'Running shell',
      }];
    }
    return [{
      type: 'tool_started',
      id,
      name: toolDisplayName(name),
      input,
    }];
  }
  if (rawType === 'tool_result') {
    const name = readString(merged.tool_name) || readString(merged.name) || readString(merged.tool) || null;
    const command = shellCommandFromData(merged);
    const input = toolInputPreview(merged);
    const status = normalizeToolResultStatus(merged.status);
    if (isShellToolName(name || '', command)) {
      if (status === 'running') {
        return [{
          type: 'exec_started',
          id,
          command: command || input || 'Running shell',
        }];
      }
      return [{
        type: 'exec_result',
        id,
        command: command || input || undefined,
        status,
        output: outputTail(merged.output, merged.result, merged.text, merged.summary),
        exitCode: readNumber(merged.exit_code),
      }];
    }
    return [{
      type: 'tool_result',
      id,
      name: name ? toolDisplayName(name) : null,
      input,
      status,
      result: toolResultText(merged),
    }];
  }
  if (rawType === 'bash_output') {
    return [{
      type: 'exec_delta',
      id,
      output: readString(merged.delta)
        || readString(merged.output)
        || readString(merged.stdout)
        || readString(merged.text),
    }];
  }
  if (rawType === 'response') {
    const text = readString(merged.delta)
      || readString(merged.text)
      || readString(merged.content);
    return text
      ? [{ type: 'assistant_delta', id: 'assistant', delta: text, provider: null, model: null }]
      : [];
  }
  return [];
}

export function projectRawEventToCodexEvents(
  event: TimelineProjectionEvent,
  fallbackIndex = 0,
): CodexChatEvent[] {
  if (event.type === 'user') {
    return [{
      type: 'user',
      id: `user:${fallbackIndex}`,
      content: readString(event.payload.content),
      attachments: Array.isArray(event.payload.attachments) ? event.payload.attachments : [],
    }];
  }

  if (event.type === 'step') {
    return projectStepEvent(readObject(event.payload), fallbackIndex);
  }

  if (event.type === 'trace') {
    return projectTraceEvent(readObject(event.payload), fallbackIndex);
  }

  if (event.type === 'typed') {
    return projectTypedStreamEvent(readObject(event.payload), fallbackIndex);
  }

  if (event.type === 'chunk') {
    const delta = readString(event.payload.delta);
    return delta
      ? [{ type: 'assistant_delta', id: 'assistant', delta, provider: null, model: null }]
      : [];
  }

  const payload = readObject(event.payload);
  const metadata = readObject(payload.metadata);
  const reply = readString(payload.reply) || readString(payload.content);
  const status = readString(payload.status).toLowerCase();
  return [{
    type: 'assistant_final',
    id: 'assistant',
    content: reply,
    isIncomplete: status === 'incomplete' || metadata.incomplete === true,
    provider: readString(metadata.effective_provider) || readString(metadata.provider) || null,
    model: readString(metadata.effective_model) || readString(metadata.model) || null,
  }];
}

export const codexChatEventInternals = {
  readObject,
  readNumber,
  readString,
};
