'use client';

export type TimelineProjectionEvent =
  | { type: 'user'; payload: { content: string; attachments?: any[] } }
  | { type: 'step'; payload: Record<string, unknown> }
  | { type: 'trace'; payload: Record<string, unknown> }
  | { type: 'typed'; payload: Record<string, unknown> & { event_type?: string } }
  | { type: 'chunk'; payload: { delta: string } }
  | { type: 'final'; payload: Record<string, unknown> };

export type CodexCellStatus =
  | 'idle'
  | 'running'
  | 'done'
  | 'error'
  | 'streaming'
  | 'incomplete'
  | 'waiting';

export type AgentActivityEvent = {
  id: string;
  type:
    | 'thinking'
    | 'connecting_hardware'
    | 'running_tool'
    | 'hardware_running'
    | 'waiting_approval'
    | 'finalizing'
    | 'error'
    | 'done';
  label: string;
  detail?: string;
  startedAt: number;
  completedAt?: number;
  status: 'active' | 'completed' | 'failed';
};

export type AgentActivityState =
  | 'idle'
  | 'thinking'
  | 'connecting'
  | 'running_tool'
  | 'waiting'
  | 'finalizing'
  | 'done'
  | 'error';

type CodexCellBase = {
  id: string;
  createdAt: string | null;
  dimmed?: boolean;
  metadata?: Record<string, unknown>;
};

export type CodexUserCell = CodexCellBase & {
  kind: 'user';
  content: string;
  attachments?: any[];
};

export type CodexAssistantCell = CodexCellBase & {
  kind: 'assistant';
  content: string;
  isStreaming: boolean;
  isIncomplete: boolean;
  effectiveProvider: string | null;
  effectiveModel: string | null;
};

export type CodexReasoningSummaryCell = CodexCellBase & {
  kind: 'reasoning_summary';
  text: string;
  activityLine: string | null;
  isStreaming: boolean;
};

export type CodexExecCell = CodexCellBase & {
  kind: 'exec';
  command: string;
  status: 'running' | 'done' | 'error';
  output: string | null;
  exitCode: number | null;
  runtimeTarget?: string | null;
  machineLabel?: string | null;
  targetKind?: string | null;
  cwd?: string | null;
  durationMs?: number | null;
  stdoutTail?: string | null;
  stderrTail?: string | null;
  approvalStatus?: string | null;
};

export type CodexToolCell = CodexCellBase & {
  kind: 'tool';
  name: string;
  input?: string | null;
  status: 'running' | 'done' | 'error';
  result: string | null;
};

export type CodexWebSearchCell = CodexCellBase & {
  kind: 'web_search';
  query: string;
  status: 'searching' | 'done' | 'error';
  result: string | null;
};

export type CodexFileChangeCell = CodexCellBase & {
  kind: 'file_change';
  filename: string;
  action: string;
  status: 'running' | 'done' | 'error';
};

export type CodexScreenshotCell = CodexCellBase & {
  kind: 'screenshot';
  caption: string;
  artifactId: string | null;
  width: number | null;
  height: number | null;
  status: 'done' | 'error';
};

export type CodexArtifactCell = CodexCellBase & {
  kind: 'artifact';
  title: string;
  artifactId: string | null;
  mimeType: string | null;
  status: 'done' | 'error';
};

export type CodexApprovalRequestCell = CodexCellBase & {
  kind: 'approval_request';
  prompt: string;
  actions: Array<'allow_once' | 'allow_session' | 'deny'>;
  status: 'waiting' | 'done' | 'error';
};

export type CodexStatusCell = CodexCellBase & {
  kind: 'status';
  label: string;
  detail: string | null;
  status: 'idle' | 'running' | 'done' | 'error';
};

export type CodexAgentActivityCell = CodexCellBase & {
  kind: 'agent_activity';
  activity: AgentActivityEvent;
};

export type CodexErrorCell = CodexCellBase & {
  kind: 'error';
  message: string;
  retryable: boolean;
};

export type CodexTraceActivityCell =
  | CodexReasoningSummaryCell
  | CodexExecCell
  | CodexToolCell
  | CodexWebSearchCell
  | CodexFileChangeCell
  | CodexScreenshotCell
  | CodexArtifactCell
  | CodexStatusCell
  | CodexAgentActivityCell;

export type CodexExecutionTraceCell = CodexCellBase & {
  kind: 'execution_trace';
  activities: CodexTraceActivityCell[];
  response: CodexAssistantCell | null;
  isStreaming: boolean;
  isIncomplete: boolean;
};

export type CodexTranscriptCell =
  | CodexUserCell
  | CodexAssistantCell
  | CodexReasoningSummaryCell
  | CodexExecCell
  | CodexToolCell
  | CodexWebSearchCell
  | CodexFileChangeCell
  | CodexScreenshotCell
  | CodexArtifactCell
  | CodexApprovalRequestCell
  | CodexStatusCell
  | CodexAgentActivityCell
  | CodexErrorCell
  | CodexExecutionTraceCell;

export type CodexStreamStatus = 'idle' | 'streaming' | 'complete' | 'aborted' | 'error';

export type CodexComposerState = {
  draft: string;
  canSend: boolean;
  canStop: boolean;
};

export type CodexTimelineProjection = {
  committedCells: CodexTranscriptCell[];
  activeCell: CodexTranscriptCell | null;
  activeTurnId: string | null;
  streamStatus: CodexStreamStatus;
  agentActivityState: AgentActivityState;
  approvalQueue: CodexApprovalRequestCell[];
  composerState: CodexComposerState;
  cells: CodexTranscriptCell[];
};

export type CodexChatEvent =
  | { type: 'user'; id: string; content: string; attachments?: any[] }
  | { type: 'reasoning_delta'; id: string; text: string; isStreaming: boolean }
  | { type: 'assistant_delta'; id: string; delta: string; provider: string | null; model: string | null }
  | { type: 'assistant_final'; id: string; content: string; isIncomplete: boolean; provider: string | null; model: string | null }
  | { type: 'tool_started'; id: string; name: string; input?: string | null }
  | { type: 'tool_result'; id: string; name: string | null; input?: string | null; status: 'running' | 'done' | 'error'; result: string | null }
  | {
      type: 'exec_started';
      id: string;
      command: string;
      runtimeTarget?: string | null;
      machineLabel?: string | null;
      targetKind?: string | null;
      cwd?: string | null;
      approvalStatus?: string | null;
    }
  | { type: 'exec_delta'; id: string; output: string }
  | {
      type: 'exec_result';
      id: string;
      command?: string;
      status: 'done' | 'error';
      output: string | null;
      exitCode: number | null;
      runtimeTarget?: string | null;
      machineLabel?: string | null;
      targetKind?: string | null;
      cwd?: string | null;
      durationMs?: number | null;
      stdoutTail?: string | null;
      stderrTail?: string | null;
      approvalStatus?: string | null;
    }
  | { type: 'web_search_started'; id: string; query: string }
  | { type: 'web_search_result'; id: string; query: string | null; status: 'done' | 'error'; result: string | null }
  | { type: 'file_change'; id: string; filename: string; action: string; status: 'running' | 'done' | 'error' }
  | { type: 'screenshot_captured'; id: string; caption: string; artifactId: string | null; width: number | null; height: number | null; status: 'done' | 'error' }
  | { type: 'artifact_created'; id: string; title: string; artifactId: string | null; mimeType: string | null; status: 'done' | 'error' }
  | { type: 'approval_request'; id: string; prompt: string; status?: 'waiting' | 'done' | 'error'; metadata?: Record<string, unknown> }
  | { type: 'status'; id: string; label: string; detail: string | null; status: 'idle' | 'running' | 'done' | 'error' }
  | { type: 'agent_activity'; activity: AgentActivityEvent }
  | { type: 'error'; id: string; message: string; retryable: boolean };

export type TimelineRow =
  | { id: string; kind: 'user'; content: string }
  | { id: string; kind: 'thinking'; text: string; activityLine: string | null; isStreaming: boolean }
  | { id: string; kind: 'tool'; name: string; status: 'running' | 'done' | 'error'; result: string | null }
  | { id: string; kind: 'file'; filename: string; action: string }
  | { id: string; kind: 'search'; query: string; status: 'searching' | 'done' }
  | { id: string; kind: 'assistant'; content: string; isStreaming: boolean; isIncomplete: boolean };
