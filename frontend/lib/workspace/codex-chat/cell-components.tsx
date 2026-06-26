'use client';

import Link from 'next/link';
import { Fragment, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Brain,
  Camera,
  Check,
  ChevronRight,
  CircleAlert,
  Copy,
  FileText,
  Paperclip,
  Search,
  ShieldCheck,
  Terminal,
  User,
  Wrench,
} from 'lucide-react';

import type { CodexTranscriptCell } from './cells';
import { stripInternalToolMarkup } from '../workstation-chat-pane-model';
import { useWorkspaceServices } from '@/lib/workspace/workspace-services';

export type CodexApprovalAction = 'allow_once' | 'allow_session' | 'deny';

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

function humanizeToken(value: string | null | undefined): string {
  const normalized = String(value ?? '').trim();
  if (!normalized) {
    return '';
  }
  return normalized
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function toolActivityLabel(name: string): string {
  const normalized = name.trim().toLowerCase();
  if (!normalized) {
    return 'Using tool';
  }
  if (normalized.startsWith('delegated to ')) {
    return `Delegated to ${name.trim().slice('delegated to '.length).trim() || 'specialist'}`;
  }
  if (normalized === 'specialist finished' || normalized.includes('specialist finished')) {
    return 'Specialist finished';
  }
  if (normalized.includes('telegram')) {
    return normalized.includes('sending') ? 'Sending Telegram' : 'Telegram';
  }
  if (normalized.includes('whatsapp')) {
    return normalized.includes('sending') ? 'Sending WhatsApp' : 'WhatsApp';
  }
  if (normalized.includes('sending email')) {
    return 'Sending email';
  }
  if (normalized.includes('browser')) {
    return 'Browser action';
  }
  if (normalized.includes('web search') || normalized.includes('search')) {
    return 'Searching web';
  }
  return humanizeToken(name) || 'Using tool';
}

function completedToolActivityLabel(name: string): string {
  const runningLabel = toolActivityLabel(name);
  if (runningLabel.startsWith('Delegated to ') || runningLabel === 'Specialist finished') {
    return runningLabel;
  }
  if (runningLabel === 'Browser action') {
    return 'Used browser';
  }
  if (runningLabel === 'Searching web') {
    return 'Searched web';
  }
  if (runningLabel.startsWith('Sending ')) {
    return runningLabel;
  }
  if (runningLabel.startsWith('Using ')) {
    return runningLabel.replace(/^Using /, 'Used ');
  }
  return runningLabel;
}

function fileActivityLabel(action: string): string {
  const normalized = action.trim().toLowerCase();
  if (
    normalized.includes('write')
    || normalized.includes('delete')
    || normalized.includes('rename')
    || normalized.includes('move')
  ) {
    return 'Updating file';
  }
  return 'Reading file';
}

function providerLabel(cell: CodexTranscriptCell): string {
  if (cell.kind !== 'assistant') {
    return '';
  }
  const parts = [
    humanizeToken(cell.effectiveProvider),
    cell.effectiveModel,
  ].filter(Boolean);
  return parts.join(' · ');
}

function routeLabel(cell: CodexTranscriptCell): string {
  if (cell.kind !== 'assistant') {
    return '';
  }
  const metadata = cell.metadata && typeof cell.metadata === 'object' ? cell.metadata : {};
  const routeDecision = metadata.route_decision && typeof metadata.route_decision === 'object' && !Array.isArray(metadata.route_decision)
    ? metadata.route_decision as Record<string, unknown>
    : {};
  const label = typeof metadata.route_label === 'string'
    ? metadata.route_label
    : typeof routeDecision.user_label === 'string'
      ? routeDecision.user_label
      : '';
  return label.trim();
}

function isLeakedMachineResultJson(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed || trimmed.length < 40 || !trimmed.startsWith('{') || !trimmed.endsWith('}')) {
    return false;
  }

  try {
    const parsed = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return false;
    }
    const record = parsed as Record<string, unknown>;
    const result = record.result;
    const resultRecord = result && typeof result === 'object' && !Array.isArray(result)
      ? result as Record<string, unknown>
      : null;

    return (
      typeof record.runtime_target === 'string'
      && (
        typeof record.gateway_id === 'string'
        || typeof record.device_id === 'string'
        || (resultRecord !== null && typeof resultRecord.command === 'string')
      )
    );
  } catch {
    return false;
  }
}

function renderInlineMarkdown(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*\*[^*]+\*\*\*|\*\*[^*]+\*\*|\*[^*]+\*|__[^_]+__|_[^_]+_)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }
    const token = match[0];
    const key = `${keyPrefix}-${nodes.length}`;
    if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith('***') && token.endsWith('***')) {
      nodes.push(<strong key={key}><em>{token.slice(3, -3)}</em></strong>);
    } else if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*') && token.endsWith('*')) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else if (token.startsWith('__') && token.endsWith('__')) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('_') && token.endsWith('_')) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else {
      nodes.push(token);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function CodeBlock({ text, language }: { text: string; language: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }).catch(() => undefined);
  };

  return (
    <div className="app-chat-markdown__code-container">
      <div className="app-chat-markdown__code-header">
        <span className="app-chat-markdown__code-language">{language || 'code'}</span>
        <button type="button" className="app-chat-markdown__code-copy" onClick={handleCopy}>
          {copied ? <Check size={12} strokeWidth={2.5} /> : <Copy size={12} strokeWidth={2.5} />}
          <span>{copied ? 'Copied' : 'Copy'}</span>
        </button>
      </div>
      <pre className="app-chat-markdown__code">
        <code>{text}</code>
      </pre>
    </div>
  );
}

function MarkdownMessage({ text }: { text: string }) {
  const lines = text.replace(/\r/g, '').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const rawLine = lines[index] ?? '';
    const line = rawLine.trim();
    if (!line) {
      index += 1;
      continue;
    }

    const hr = line.match(/^([-*_])\1{2,}\s*$/);
    if (hr) {
      blocks.push(<hr key={`hr-${index}`} className="app-chat-markdown__hr" />);
      index += 1;
      continue;
    }

    const codeFence = line.match(/^```([\w-]+)?\s*$/);
    if (codeFence) {
      const language = codeFence[1] || '';
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !(lines[index] ?? '').trim().startsWith('```')) {
        codeLines.push(lines[index] ?? '');
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      blocks.push(
        <CodeBlock
          key={`code-${index}`}
          text={codeLines.join('\n')}
          language={language}
        />,
      );
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1]?.length ?? 2, 4);
      const content = heading[2] ?? '';
      const headingContent = renderInlineMarkdown(content, `heading-${index}`);
      const headingClassName = 'app-chat-markdown__heading';
      blocks.push(
        level <= 1
          ? <h2 key={`heading-${index}`} className={headingClassName}>{headingContent}</h2>
          : level === 2
            ? <h3 key={`heading-${index}`} className={headingClassName}>{headingContent}</h3>
            : <h4 key={`heading-${index}`} className={headingClassName}>{headingContent}</h4>,
      );
      index += 1;
      continue;
    }

    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = (lines[index] ?? '').trim().match(/^[-*]\s+(.+)$/);
        if (!item) {
          break;
        }
        items.push(item[1] ?? '');
        index += 1;
      }
      blocks.push(
        <ul key={`ul-${index}`} className="app-chat-markdown__list">
          {items.map((item, itemIndex) => (
            <li key={`ul-${index}-${itemIndex}`}>
              {renderInlineMarkdown(item, `ul-${index}-${itemIndex}`)}
            </li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = (lines[index] ?? '').trim().match(/^\d+[.)]\s+(.+)$/);
        if (!item) {
          break;
        }
        items.push(item[1] ?? '');
        index += 1;
      }
      blocks.push(
        <ol key={`ol-${index}`} className="app-chat-markdown__list">
          {items.map((item, itemIndex) => (
            <li key={`ol-${index}-${itemIndex}`}>
              {renderInlineMarkdown(item, `ol-${index}-${itemIndex}`)}
            </li>
          ))}
        </ol>,
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length) {
      const nextLine = (lines[index] ?? '').trim();
      if (
        !nextLine
        || /^```/.test(nextLine)
        || /^#{1,4}\s+/.test(nextLine)
        || /^[-*]\s+/.test(nextLine)
        || /^\d+[.)]\s+/.test(nextLine)
      ) {
        break;
      }
      paragraphLines.push(nextLine);
      index += 1;
    }
    blocks.push(
      <p key={`p-${index}`} className="app-chat-markdown__paragraph">
        {paragraphLines.map((paragraphLine, paragraphIndex) => (
          <Fragment key={`p-${index}-${paragraphIndex}`}>
            {paragraphIndex > 0 ? <br /> : null}
            {renderInlineMarkdown(paragraphLine, `p-${index}-${paragraphIndex}`)}
          </Fragment>
        ))}
      </p>,
    );
  }

  return <div className="app-chat-markdown">{blocks}</div>;
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

function compactSystemDetail(value: string | null | undefined, fallback: string): string {
  const trimmed = String(value ?? '').replace(/\s+/g, ' ').trim();
  if (!trimmed) {
    return fallback;
  }
  const lower = trimmed.toLowerCase();
  const isJsonLike = (trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'));
  const looksDebugLike =
    isJsonLike
    || lower.startsWith('event:')
    || lower.startsWith('data:')
    || lower.includes('"event_type"')
    || lower.includes('"payload"')
    || lower.includes('"metadata"')
    || lower.includes('trace_id')
    || lower.includes('tool_call_id')
    || lower.includes('activity_event_id');
  if (looksDebugLike) {
    return fallback;
  }
  return trimmed.length > 140 ? `${trimmed.slice(0, 137).trimEnd()}…` : trimmed;
}

function compactShellCommand(command: string): string {
  return command.replace(/\s+/g, ' ').trim().toLowerCase();
}

function shellPathCopy(command: string, status: 'running' | 'done' | 'error'): { primary: string; path: string[] } {
  const normalized = compactShellCommand(command);
  const action = status === 'running' ? 'Checking' : status === 'error' ? 'Needs review' : 'Checked';
  if (normalized === 'pwd') {
    return {
      primary: `${action} folder`,
      path: ['Sage', 'This Device', 'Folder'],
    };
  }
  if (normalized.includes('sw_vers') || normalized.includes('sysctl -n machdep.cpu.brand_string') || normalized.includes('hw.memsize')) {
    return {
      primary: `${action} this Mac`,
      path: ['Sage', 'This Device', 'Hardware'],
    };
  }
  return {
    primary: status === 'error' ? 'Local check failed' : `${action} this device`,
    path: ['Sage', 'This Device', 'Result'],
  };
}

function execStatusCopy(status: 'running' | 'done' | 'error'): { label: string; tone: 'running' | 'completed' | 'failed' } {
  if (status === 'running') {
    return { label: 'running', tone: 'running' };
  }
  if (status === 'error') {
    return { label: 'failed', tone: 'failed' };
  }
  return { label: 'completed', tone: 'completed' };
}

function executionTargetLabel(cell: Extract<CodexTranscriptCell, { kind: 'exec' }>): string {
  const explicit = String(cell.machineLabel ?? '').trim();
  if (explicit) {
    return explicit;
  }
  const targetKind = String(cell.targetKind ?? '').trim().toLowerCase();
  const runtimeTarget = String(cell.runtimeTarget ?? '').trim().toLowerCase();
  if (targetKind.includes('vps') || runtimeTarget.includes('vps') || runtimeTarget.includes('self_hosted')) {
    return 'VPS';
  }
  if (runtimeTarget.includes('hosted') || runtimeTarget.includes('cloud')) {
    return 'Cloud worker';
  }
  if (targetKind.includes('mac') || runtimeTarget.includes('local') || runtimeTarget.includes('user_device')) {
    return 'This Mac';
  }
  return 'This Device';
}

function executionTargetPath(cell: Extract<CodexTranscriptCell, { kind: 'exec' }>): string[] {
  return ['Sage', executionTargetLabel(cell), 'Terminal'];
}

function formatDurationMs(value: number | null | undefined): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return null;
  }
  if (value < 1000) {
    return `${Math.round(value)}ms`;
  }
  const seconds = value / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(1).replace(/\.0$/, '')}s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

function ExecutionMetaRow({ label, value, tone }: { label: string; value: ReactNode; tone?: 'success' | 'danger' }) {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  return (
    <div className="app-chat-execution-card__meta-row">
      <span>{label}</span>
      <strong className={tone ? `app-chat-execution-card__meta-value--${tone}` : undefined}>{value}</strong>
    </div>
  );
}

function ExecutionOutputBlock({ label, value }: { label: string; value: string | null | undefined }) {
  const text = String(value ?? '').trim();
  if (!text) {
    return null;
  }
  return (
    <div className="app-chat-execution-card__output-block">
      <span className="app-chat-execution-card__section-title">{label}</span>
      <pre className="app-chat-execution-card__output"><code>{text}</code></pre>
    </div>
  );
}

type ExecutionTraceCellRecord = Extract<CodexTranscriptCell, { kind: 'execution_trace' }>;
type TraceActivityCellRecord = ExecutionTraceCellRecord['activities'][number];

function traceActivityStatus(cell: TraceActivityCellRecord): 'running' | 'done' | 'error' {
  if (cell.kind === 'reasoning_summary') {
    return cell.isStreaming ? 'running' : 'done';
  }
  if (cell.kind === 'web_search') {
    return cell.status === 'searching' ? 'running' : cell.status;
  }
  if (cell.kind === 'agent_activity') {
    return agentActivityState(cell.activity.status);
  }
  if ('status' in cell) {
    if (cell.status === 'idle') {
      return 'running';
    }
    return cell.status;
  }
  return 'done';
}

function traceActivityLabel(cell: TraceActivityCellRecord): string {
  switch (cell.kind) {
    case 'exec':
      return 'Command';
    case 'tool':
      return toolActivityLabel(cell.name || 'Tool');
    case 'web_search':
      return 'Web search';
    case 'file_change':
      return fileActivityLabel(cell.action || 'File');
    case 'screenshot':
      return 'Screenshot';
    case 'artifact':
      return 'Artifact';
    case 'status':
      return statusPrimaryLabel(cell.label || 'Status');
    case 'agent_activity':
      return cell.activity.label;
    default:
      return 'Tool';
  }
}

function traceActivityInput(cell: TraceActivityCellRecord): string {
  switch (cell.kind) {
    case 'exec':
      return cell.command;
    case 'tool':
      return String(cell.input ?? cell.name ?? '').trim();
    case 'web_search':
      return cell.query;
    case 'file_change':
      return cell.filename;
    case 'screenshot':
      return cell.caption;
    case 'artifact':
      return cell.title;
    case 'status':
      return cell.detail || cell.label;
    case 'agent_activity':
      return cell.activity.detail || cell.activity.label;
    default:
      return '';
  }
}

function traceActivityResult(cell: TraceActivityCellRecord): string | null {
  switch (cell.kind) {
    case 'exec':
      return cell.stdoutTail || cell.output || cell.stderrTail || null;
    case 'tool':
      return cell.result;
    case 'web_search':
      return cell.result;
    case 'file_change':
      return cell.status === 'done' ? cell.action : null;
    case 'screenshot':
      return cell.caption;
    case 'artifact':
      return cell.title;
    case 'status':
      return cell.detail;
    case 'agent_activity':
      return cell.activity.status === 'completed' ? (cell.activity.detail ?? 'Done') : null;
    default:
      return null;
  }
}

function traceSummaryParts(activities: TraceActivityCellRecord[]): string[] {
  const searches = activities.filter((cell) => cell.kind === 'web_search').length;
  const commands = activities.filter((cell) => cell.kind === 'exec').length;
  const files = activities.filter((cell) => cell.kind === 'file_change').length;
  const screenshots = activities.filter((cell) => cell.kind === 'screenshot').length;
  const genericTools = activities.filter((cell) => cell.kind === 'tool').length;
  const artifacts = activities.filter((cell) => cell.kind === 'artifact').length;
  const agentActivities = activities.filter((cell) => cell.kind === 'agent_activity');
  const parts: string[] = [];
  const activeAgentActivity = agentActivities.find((cell) => cell.activity.status === 'active');
  if (activeAgentActivity) {
    parts.push(activeAgentActivity.activity.label);
  } else if (agentActivities.length > 0) {
    const latest = agentActivities.at(-1);
    if (latest) {
      parts.push(latest.activity.label);
    }
  }
  if (searches > 0) {
    parts.push(`Searched ${searches} ${searches === 1 ? 'query' : 'queries'}`);
  }
  if (commands > 0) {
    parts.push(`Ran ${commands} ${commands === 1 ? 'command' : 'commands'}`);
  }
  if (files > 0) {
    parts.push(`${files === 1 ? 'Checked' : 'Checked'} ${files} ${files === 1 ? 'file' : 'files'}`);
  }
  if (screenshots > 0) {
    parts.push(`Captured ${screenshots} ${screenshots === 1 ? 'screenshot' : 'screenshots'}`);
  }
  if (genericTools > 0) {
    parts.push(`Used ${genericTools} ${genericTools === 1 ? 'tool' : 'tools'}`);
  }
  if (artifacts > 0) {
    parts.push(`Created ${artifacts} ${artifacts === 1 ? 'artifact' : 'artifacts'}`);
  }
  return parts.length > 0 ? parts : [`Completed ${activities.length} ${activities.length === 1 ? 'step' : 'steps'}`];
}

function TraceStatusPill({ status }: { status: 'running' | 'done' | 'error' }) {
  return (
    <span className={`app-agent-trace-status app-agent-trace-status--${status}`}>
      {status === 'running' ? 'loading' : status}
    </span>
  );
}

function ThoughtProcessPanel({
  cell,
}: {
  cell: Extract<CodexTranscriptCell, { kind: 'reasoning_summary' }>;
}) {
  const text = visibleThinkingContent(cell.text);
  if (!text) {
    return null;
  }

  return <pre className="app-agent-trace-thinking__body">{text}</pre>;
}

function TerminalOutputBlock({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const text = value.trim();
  if (!text) {
    return null;
  }
  return (
    <div className="app-agent-terminal">
      <button
        type="button"
        className="app-agent-terminal__copy"
        onClick={() => {
          void navigator.clipboard?.writeText(text).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1200);
          }).catch(() => undefined);
        }}
      >
        <Copy size={12} strokeWidth={2} aria-hidden="true" />
        <span>{copied ? 'Copied' : 'Copy'}</span>
      </button>
      <pre className="app-agent-terminal__output"><code>{text}</code></pre>
    </div>
  );
}

function ToolTraceCard({ cell }: { cell: TraceActivityCellRecord }) {
  const services = useWorkspaceServices();
  const [expanded, setExpanded] = useState(false);
  if (cell.kind === 'reasoning_summary') {
    return null;
  }
  const status = traceActivityStatus(cell);
  const label = traceActivityLabel(cell);
  // For agent_activity, input and result are the same field (detail).
  // Show detail only once — as the expandable result, not as the code input block.
  const isAgentActivity = cell.kind === 'agent_activity';
  const input = isAgentActivity ? null : traceActivityInput(cell);
  const result = traceActivityResult(cell);
  const isLongResult = Boolean(result && result.length > 200);
  const showInlineResult = Boolean(result && !isLongResult);
  const showExpandableResult = Boolean(result && isLongResult);
  const terminalText = cell.kind === 'exec'
    ? [cell.stdoutTail || cell.output || '', cell.stderrTail || ''].filter(Boolean).join('\n')
    : '';
  const artifactId = cell.kind === 'screenshot' || cell.kind === 'artifact'
    ? cell.artifactId
    : null;
  const artifactHref = artifactId ? services.client.artifactDownloadUrl(artifactId) : null;

  return (
    <section className={`app-agent-tool-card app-agent-tool-card--${status}`}>
      <div className="app-agent-tool-card__header">
        <span className="app-agent-tool-card__label">{label}</span>
        <TraceStatusPill status={status} />
      </div>
      {input ? (
        <pre className="app-agent-tool-card__input"><code>{input}</code></pre>
      ) : null}
      {cell.kind === 'exec' && terminalText ? (
        <TerminalOutputBlock value={terminalText} />
      ) : null}
      {showInlineResult && cell.kind !== 'exec' ? (
        <div className="app-agent-tool-card__result">{result}</div>
      ) : null}
      {showExpandableResult && cell.kind !== 'exec' ? (
        <>
          <button
            type="button"
            className="app-agent-tool-card__toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? 'Hide output' : 'View output'}
          </button>
          {expanded ? (
            <pre className="app-agent-tool-card__output"><code>{result}</code></pre>
          ) : null}
        </>
      ) : null}
      {artifactHref ? (
        <Link href={artifactHref} target="_blank" rel="noreferrer" className="app-agent-tool-card__link">
          Open
        </Link>
      ) : null}
    </section>
  );
}

export function ExecutionTraceCell({ cell }: { cell: ExecutionTraceCellRecord }) {
  const [expanded, setExpanded] = useState(false);
  const reasoningCell = cell.activities.find(
    (activity): activity is Extract<CodexTranscriptCell, { kind: 'reasoning_summary' }> => activity.kind === 'reasoning_summary',
  ) ?? null;
  const thinkingCell = reasoningCell && visibleThinkingContent(reasoningCell.text) ? reasoningCell : null;
  const toolActivities = cell.activities.filter((activity) => activity.kind !== 'reasoning_summary');
  const summary = useMemo(
    () => {
      if (toolActivities.length > 0) {
        return traceSummaryParts(toolActivities).join(' · ');
      }
      return cell.isStreaming ? 'Thinking' : 'Thought process';
    },
    [cell.isStreaming, toolActivities],
  );
  const hasThinking = Boolean(thinkingCell) || cell.isStreaming;
  const showThinking = Boolean(thinkingCell) && expanded;

  useEffect(() => {
    if (cell.isStreaming) {
      setExpanded(false);
    }
  }, [cell.isStreaming]);

  return (
    <div
      data-chat-role={cell.response ? 'assistant' : 'system'}
      className={`app-agent-trace-turn${cell.isStreaming ? ' app-agent-trace-turn--streaming' : ''}${cell.dimmed ? ' app-agent-trace-turn--dimmed' : ''}`}
    >
      {hasThinking ? (
        <button
          type="button"
          className="app-agent-trace-summary"
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
        >
          <ChevronRight
            size={13}
            strokeWidth={2}
            className={`app-agent-trace-summary__chevron${expanded ? ' app-agent-trace-summary__chevron--expanded' : ''}`}
            aria-hidden="true"
          />
          <span>{summary}</span>
        </button>
      ) : toolActivities.length > 0 ? (
        <span className="app-agent-trace-summary app-agent-trace-summary--static">
          <span>{summary}</span>
        </span>
      ) : null}
      {showThinking && thinkingCell ? (
        <div className="app-agent-trace-stack">
          <ThoughtProcessPanel cell={thinkingCell} />
        </div>
      ) : null}
      {cell.response ? <AssistantCell cell={cell.response} /> : null}
    </div>
  );
}

function statusPrimaryLabel(label: string): string {
  const normalized = label.trim().toLowerCase();
  if (!normalized) {
    return 'Done';
  }
  if (normalized === 'final outcome ready' || normalized === 'done') {
    return 'Done';
  }
  if (normalized.includes('approval')) {
    return 'Approval needed';
  }
  return humanizeToken(label) || 'Done';
}

function agentActivityKind(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'agent_activity';
}

function agentActivityState(status: 'active' | 'completed' | 'failed'): 'running' | 'done' | 'error' {
  if (status === 'failed') {
    return 'error';
  }
  if (status === 'completed') {
    return 'done';
  }
  return 'running';
}

function SystemInlineRow({
  icon,
  kind,
  primary,
  secondary,
  path,
  state,
  dimmed,
  action,
}: {
  icon: ReactNode;
  kind: string;
  primary: string;
  secondary: string;
  path?: string[];
  state: 'running' | 'done' | 'error';
  dimmed: boolean;
  action?: ReactNode;
}) {
  return (
    <article
      data-chat-role="system"
      data-chat-activity-kind={kind}
      className={`app-chat-system-row app-chat-system-row--${state}${dimmed ? ' app-chat-system-row--dimmed' : ''}`}
    >
      <span className="app-chat-system-row__icon" aria-hidden="true">{icon}</span>
      <span className="app-chat-system-row__primary">{primary}</span>
      {path && path.length > 0 ? (
        <span className="app-chat-system-row__path" aria-label={path.join(' to ')}>
          {path.map((item, index) => (
            <Fragment key={`${item}-${index}`}>
              {index > 0 ? <span className="app-chat-system-row__path-edge" aria-hidden="true" /> : null}
              <span className="app-chat-system-row__path-node">{item}</span>
            </Fragment>
          ))}
        </span>
      ) : (
        <span className="app-chat-system-row__secondary">{secondary}</span>
      )}
      {action ? <span className="app-chat-system-row__action">{action}</span> : null}
    </article>
  );
}

export function UserCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'user' }> }) {
  const timestamp = formatTimestamp(cell.createdAt);
  const text = cell.content.trim();
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }).catch(() => undefined);
  };
  return (
    <article data-chat-role="user" className="app-chat-message">
      <div className="app-chat-message__content">{text}</div>
      <div className="app-chat-message__footer">
        {timestamp ? (
          <time className="app-chat-message__timestamp" dateTime={cell.createdAt ?? undefined}>
            {timestamp}
          </time>
        ) : null}
        <button
          type="button"
          className="app-chat-message__copy-button"
          onClick={handleCopy}
          title="Copy message"
        >
          {copied ? <Check size={12} strokeWidth={2.5} /> : <Copy size={12} strokeWidth={2.5} />}
        </button>
      </div>
    </article>
  );
}

export function AssistantCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'assistant' }> }) {
  const timestamp = formatTimestamp(cell.createdAt);
  const effectiveLabel = providerLabel(cell);
  const taskRouteLabel = routeLabel(cell);
  const text = stripInternalToolMarkup(cell.content);
  const [copied, setCopied] = useState(false);
  if ((!text && !cell.isStreaming) || isLeakedMachineResultJson(text)) {
    return null;
  }
  const handleCopy = () => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    }).catch(() => undefined);
  };

  return (
    <article data-chat-role="assistant" className="app-chat-message">
      <div className="app-chat-message__content">
        <MarkdownMessage text={text} />
      </div>
      <div className="app-chat-message__footer">
        <div className="app-chat-message__meta">
          {effectiveLabel ? (
            <span className="app-chat-message__provider">{effectiveLabel}</span>
          ) : null}
          {taskRouteLabel ? (
            <span className="app-chat-message__provider">{taskRouteLabel}</span>
          ) : null}
          {cell.isIncomplete ? (
            <span className="app-chat-message__status">Incomplete</span>
          ) : null}
          {timestamp ? (
            <time className="app-chat-message__timestamp" dateTime={cell.createdAt ?? undefined}>
              {timestamp}
            </time>
          ) : null}
        </div>
        <button
          type="button"
          className="app-chat-message__copy-button"
          onClick={handleCopy}
          title="Copy message"
        >
          {copied ? <Check size={12} strokeWidth={2.5} /> : <Copy size={12} strokeWidth={2.5} />}
        </button>
      </div>
    </article>
  );
}

export function ReasoningSummaryCell({
  cell,
}: {
  cell: Extract<CodexTranscriptCell, { kind: 'reasoning_summary' }>;
}) {
  const [expanded, setExpanded] = useState(false);
  const text = visibleThinkingContent(cell.text);

  useEffect(() => {
    if (cell.isStreaming) {
      setExpanded(false);
    }
  }, [cell.isStreaming]);

  if (!text) {
    return null;
  }

  return (
    <article
      data-chat-role="system"
      data-chat-activity-kind="thinking"
      className={`app-chat-thinking-row${cell.isStreaming ? ' app-chat-thinking-row--streaming' : ''}${cell.dimmed ? ' app-chat-thinking-row--dimmed' : ''}`}
    >
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
        <span className="app-chat-thinking-row__label">{cell.isStreaming ? 'Thinking' : 'Thought process'}</span>
      </button>
      {expanded ? (
        <div className="app-chat-thinking-row__detail">
          <pre className="app-chat-thinking-row__detail-text">{text}</pre>
        </div>
      ) : null}
    </article>
  );
}

export function ExecCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'exec' }> }) {
  const [expanded, setExpanded] = useState(false);
  const pathCopy = shellPathCopy(cell.command, cell.status);
  const statusCopy = execStatusCopy(cell.status);
  const command = compactSystemDetail(cell.command, 'Running shell');
  const targetPath = executionTargetPath(cell);
  const stdoutTail = cell.stdoutTail ?? cell.output;
  const stderrTail = cell.stderrTail;
  const duration = formatDurationMs(cell.durationMs);
  const hasDetails = Boolean(
    cell.cwd
    || cell.exitCode !== null
    || duration
    || stdoutTail
    || stderrTail
    || cell.approvalStatus
    || cell.runtimeTarget
    || cell.targetKind
  );

  return (
    <article
      data-chat-role="system"
      data-chat-activity-kind="shell"
      className={`app-chat-execution-card app-chat-execution-card--${statusCopy.tone}${expanded ? ' app-chat-execution-card--expanded' : ''}${cell.dimmed ? ' app-chat-execution-card--dimmed' : ''}`}
    >
      <button
        type="button"
        className="app-chat-execution-card__summary"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <span className="app-chat-execution-card__accent" aria-hidden="true" />
        <span className="app-chat-execution-card__terminal" aria-hidden="true">
          <Terminal size={13} strokeWidth={2.1} />
        </span>
        <span className="app-chat-execution-card__copy">
          <span className="app-chat-execution-card__primary">
            {pathCopy.primary}
          </span>
          <span className="app-chat-execution-card__path" aria-label={targetPath.join(' to ')}>
            {targetPath.map((item, index) => (
              <Fragment key={`${item}-${index}`}>
                {index > 0 ? <span className="app-chat-execution-card__path-edge" aria-hidden="true" /> : null}
                <span className="app-chat-execution-card__path-node">{item}</span>
              </Fragment>
            ))}
          </span>
          <span className="app-chat-execution-card__command">{command}</span>
        </span>
        <span className="app-chat-execution-card__status">{statusCopy.label}</span>
        <ChevronRight
          size={13}
          strokeWidth={2}
          className={`app-chat-execution-card__chevron${expanded ? ' app-chat-execution-card__chevron--expanded' : ''}`}
          aria-hidden="true"
        />
      </button>
      {expanded && hasDetails ? (
        <div className="app-chat-execution-card__details">
          <div className="app-chat-execution-card__section">
            <span className="app-chat-execution-card__section-title">Command</span>
            <pre className="app-chat-execution-card__command-block"><code>{cell.command || 'Command'}</code></pre>
          </div>
          <div className="app-chat-execution-card__meta">
            <ExecutionMetaRow label="Target" value={executionTargetLabel(cell)} />
            <ExecutionMetaRow label="Directory" value={cell.cwd ?? null} />
            <ExecutionMetaRow
              label="Exit code"
              value={cell.exitCode === null ? null : cell.exitCode}
              tone={cell.exitCode === null ? undefined : cell.exitCode === 0 ? 'success' : 'danger'}
            />
            <ExecutionMetaRow label="Duration" value={duration} />
            <ExecutionMetaRow label="Approval" value={cell.approvalStatus ?? null} />
          </div>
          <ExecutionOutputBlock label="stdout" value={stdoutTail} />
          <ExecutionOutputBlock label="stderr" value={stderrTail} />
        </div>
      ) : null}
    </article>
  );
}

export function ToolCallCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'tool' }> }) {
  return (
    <SystemInlineRow
      kind={toolActivityLabel(cell.name || '').toLowerCase().replace(/\s+/g, '_')}
      icon={cell.status === 'done'
        ? <Check size={14} strokeWidth={2} />
        : cell.status === 'error'
          ? <CircleAlert size={14} strokeWidth={2} />
          : <Wrench size={14} strokeWidth={1.9} />}
      primary={cell.status === 'done' ? completedToolActivityLabel(cell.name || '') : toolActivityLabel(cell.name || '')}
      secondary={compactSystemDetail(cell.result, cell.status === 'done' ? 'Done' : cell.status === 'error' ? 'Failed' : (cell.name || 'Running'))}
      state={cell.status}
      dimmed={cell.dimmed === true}
    />
  );
}

export function WebSearchCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'web_search' }> }) {
  const state = cell.status === 'searching' ? 'running' : cell.status;
  return (
    <SystemInlineRow
      kind="search"
      icon={cell.status === 'done'
        ? <Check size={14} strokeWidth={2} />
        : cell.status === 'error'
          ? <CircleAlert size={14} strokeWidth={2} />
          : <Search size={14} strokeWidth={1.9} />}
      primary={cell.status === 'done' ? 'Searched web' : 'Searching web'}
      secondary={compactSystemDetail(cell.query, cell.status === 'done' ? 'Done' : cell.status === 'error' ? 'Failed' : 'Searching')}
      state={state}
      dimmed={cell.dimmed === true}
    />
  );
}

export function FileChangeCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'file_change' }> }) {
  return (
    <SystemInlineRow
      kind="file"
      icon={cell.status === 'done'
        ? <Check size={14} strokeWidth={2} />
        : cell.status === 'error'
          ? <CircleAlert size={14} strokeWidth={2} />
          : <FileText size={14} strokeWidth={1.9} />}
      primary={cell.status === 'done' ? fileActivityLabel(cell.action || '').replace(/^Reading /, 'Read ').replace(/^Updating /, 'Updated ') : fileActivityLabel(cell.action || '')}
      secondary={compactSystemDetail(cell.filename, cell.status === 'done' ? 'Done' : 'File')}
      state={cell.status}
      dimmed={cell.dimmed === true}
    />
  );
}

export function ScreenshotCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'screenshot' }> }) {
  const services = useWorkspaceServices();
  const artifactHref = cell.artifactId ? services.client.artifactDownloadUrl(cell.artifactId) : null;
  return (
    <SystemInlineRow
      kind="artifact"
      icon={cell.status === 'done'
        ? <Camera size={14} strokeWidth={1.9} />
        : <CircleAlert size={14} strokeWidth={2} />}
      primary={cell.status === 'done' ? 'Captured screenshot' : 'Screenshot/artifact'}
      secondary={compactSystemDetail(cell.caption, cell.status === 'done' ? 'Captured' : 'Failed')}
      state={cell.status}
      dimmed={cell.dimmed === true}
      action={artifactHref ? (
        <Link href={artifactHref} target="_blank" rel="noreferrer">
          Open
        </Link>
      ) : null}
    />
  );
}

export function ArtifactCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'artifact' }> }) {
  const services = useWorkspaceServices();
  const artifactHref = cell.artifactId ? services.client.artifactDownloadUrl(cell.artifactId) : null;
  return (
    <SystemInlineRow
      kind="artifact"
      icon={cell.status === 'done'
        ? <Paperclip size={14} strokeWidth={1.9} />
        : <CircleAlert size={14} strokeWidth={2} />}
      primary={cell.status === 'done' ? 'Created artifact' : 'Artifact failed'}
      secondary={compactSystemDetail(cell.title || cell.mimeType, cell.status === 'done' ? 'Created' : 'Failed')}
      state={cell.status}
      dimmed={cell.dimmed === true}
      action={artifactHref ? (
        <Link href={artifactHref} target="_blank" rel="noreferrer">
          Open
        </Link>
      ) : null}
    />
  );
}

export function ApprovalCell({
  cell,
  resolvingApprovalId,
  onResolveApproval,
}: {
  cell: Extract<CodexTranscriptCell, { kind: 'approval_request' }>;
  resolvingApprovalId?: string | null;
  onResolveApproval?: (approvalId: string, action: CodexApprovalAction) => void;
}) {
  const approvalId = typeof cell.metadata?.approval_id === 'string'
    ? cell.metadata.approval_id
    : typeof cell.metadata?.id === 'string'
      ? cell.metadata.id
      : cell.id;
  const approvalCode = typeof cell.metadata?.code === 'string' && cell.metadata.code.trim()
    ? cell.metadata.code.trim()
    : approvalId;
  const decision = typeof cell.metadata?.decision === 'string'
    ? cell.metadata.decision.trim().toLowerCase()
    : typeof cell.metadata?.resolution === 'string'
      ? cell.metadata.resolution.trim().toLowerCase()
      : '';
  const denied = cell.status === 'error' && /reject|deny|denied|rejected|cancel|stop|abort|no/.test(decision);
  const primary = cell.status === 'waiting'
    ? 'Approval needed'
    : cell.status === 'done'
      ? 'Approval approved'
      : denied
        ? 'Approval denied'
        : 'Approval failed';
  const fallback = cell.status === 'waiting'
    ? 'Choose 1 allow once, 2 allow session, or 3 deny'
    : cell.status === 'done'
      ? 'Approved'
      : denied
        ? 'Denied'
        : 'Failed';
  const resolving = Boolean(resolvingApprovalId && resolvingApprovalId === approvalId);
  const canResolve = Boolean(approvalId && onResolveApproval && cell.status === 'waiting');
  return (
    <article
      data-chat-role="system"
      data-chat-activity-kind="approval"
      className={`app-chat-approval-cell app-chat-system-row--${cell.status === 'waiting' ? 'running' : cell.status}${cell.dimmed ? ' app-chat-system-row--dimmed' : ''}`}
    >
      <span className="app-chat-system-row__icon" aria-hidden="true">
        <ShieldCheck size={14} strokeWidth={1.9} />
      </span>
      <div className="app-chat-approval-cell__copy">
        <span className="app-chat-system-row__primary">{primary}</span>
        <span className="app-chat-system-row__secondary">
          {compactSystemDetail(cell.prompt, fallback)}
          {approvalCode ? ` · Code ${approvalCode}` : ''}
        </span>
      </div>
      {canResolve ? (
        <div className="app-chat-approval-cell__actions">
          <button
            type="button"
            disabled={resolving}
            onClick={() => onResolveApproval?.(approvalId, 'allow_once')}
          >
            <kbd>1</kbd>
            <span>{resolving ? 'Allowing' : 'Allow once'}</span>
          </button>
          <button
            type="button"
            disabled={resolving}
            onClick={() => onResolveApproval?.(approvalId, 'allow_session')}
          >
            <kbd>2</kbd>
            <span>Allow session</span>
          </button>
          <button
            type="button"
            disabled={resolving}
            onClick={() => onResolveApproval?.(approvalId, 'deny')}
          >
            <kbd>3</kbd>
            <span>Deny</span>
          </button>
        </div>
      ) : null}
    </article>
  );
}

export function StatusCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'status' }> }) {
  return (
    <SystemInlineRow
      kind={statusPrimaryLabel(cell.label || '').toLowerCase().replace(/\s+/g, '_')}
      icon={cell.status === 'done'
        ? <Check size={14} strokeWidth={2} />
        : cell.status === 'error'
          ? <CircleAlert size={14} strokeWidth={2} />
          : <Brain size={14} strokeWidth={1.9} />}
      primary={statusPrimaryLabel(cell.label || 'Status')}
      secondary={compactSystemDetail(cell.detail, cell.status === 'done' ? 'Done' : cell.status === 'error' ? 'Failed' : 'Running')}
      state={cell.status === 'idle' ? 'running' : cell.status}
      dimmed={cell.dimmed === true}
    />
  );
}

export function AgentActivityCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'agent_activity' }> }) {
  const [expanded, setExpanded] = useState(false);
  const { activity } = cell;
  const state = agentActivityState(activity.status);
  const duration = formatDurationMs(
    typeof activity.completedAt === 'number'
      ? activity.completedAt - activity.startedAt
      : null,
  );
  const hasDetails = Boolean(activity.detail || duration);
  return (
    <article
      data-chat-role="system"
      data-chat-activity-kind={agentActivityKind(activity.type)}
      data-agent-activity-state={state}
      className={`app-agent-activity-row app-chat-system-row--${state}${cell.dimmed ? ' app-chat-system-row--dimmed' : ''}`}
    >
      <button
        type="button"
        className="app-agent-activity-row__summary"
        aria-expanded={expanded}
        disabled={!hasDetails}
        onClick={() => {
          if (hasDetails) {
            setExpanded((value) => !value);
          }
        }}
      >
        <span className="app-chat-system-row__icon" aria-hidden="true">
          {state === 'done'
            ? <Check size={14} strokeWidth={2} />
            : state === 'error'
              ? <CircleAlert size={14} strokeWidth={2} />
              : <Terminal size={14} strokeWidth={1.9} />}
        </span>
        <span className="app-chat-system-row__primary">{activity.label}</span>
        {hasDetails ? (
          <ChevronRight
            size={13}
            strokeWidth={2}
            className={`app-agent-activity-row__chevron${expanded ? ' app-agent-activity-row__chevron--expanded' : ''}`}
            aria-hidden="true"
          />
        ) : null}
      </button>
      {expanded && hasDetails ? (
        <div className="app-agent-activity-row__details">
          {activity.detail ? <span>{activity.detail}</span> : null}
          {duration ? <span>Duration {duration}</span> : null}
        </div>
      ) : null}
    </article>
  );
}

export function ErrorCell({ cell }: { cell: Extract<CodexTranscriptCell, { kind: 'error' }> }) {
  const actionHref = typeof cell.metadata?.action_href === 'string' ? cell.metadata.action_href : '';
  const actionLabel = typeof cell.metadata?.action_label === 'string' ? cell.metadata.action_label : '';
  const lowerMessage = cell.message.toLowerCase();
  const message = lowerMessage.includes('ollama')
    || lowerMessage.includes('selected provider')
    || lowerMessage.includes('selected for chat')
    || lowerMessage.includes('local-only')
    ? 'Choose the default Sage route, connect a model account, or connect Agent Computer.'
    : cell.message;
  return (
    <article data-chat-role="system" className="app-chat-transcript-error">
      <span className="app-chat-transcript-error__icon" aria-hidden="true">
        <CircleAlert size={14} strokeWidth={1.9} />
      </span>
      <div className="app-chat-transcript-error__copy">
        <strong>Sage route needs attention</strong>
        <span>{message}</span>
      </div>
      {actionHref && actionLabel ? (
        <Link href={actionHref} className="app-chat-transcript-error__link">
          {actionLabel}
        </Link>
      ) : null}
    </article>
  );
}

export function CodexChatCell({
  cell,
  resolvingApprovalId,
  onResolveApproval,
}: {
  cell: CodexTranscriptCell;
  resolvingApprovalId?: string | null;
  onResolveApproval?: (approvalId: string, action: CodexApprovalAction) => void;
}) {
  switch (cell.kind) {
    case 'user':
      return <UserCell cell={cell} />;
    case 'assistant':
      return <AssistantCell cell={cell} />;
    case 'execution_trace':
      return <ExecutionTraceCell cell={cell} />;
    case 'reasoning_summary':
      return <ReasoningSummaryCell cell={cell} />;
    case 'exec':
      return <ExecCell cell={cell} />;
    case 'tool':
      return <ToolCallCell cell={cell} />;
    case 'web_search':
      return <WebSearchCell cell={cell} />;
    case 'file_change':
      return <FileChangeCell cell={cell} />;
    case 'screenshot':
      return <ScreenshotCell cell={cell} />;
    case 'artifact':
      return <ArtifactCell cell={cell} />;
    case 'approval_request':
      return (
        <ApprovalCell
          cell={cell}
          resolvingApprovalId={resolvingApprovalId}
          onResolveApproval={onResolveApproval}
        />
      );
    case 'status':
      return <StatusCell cell={cell} />;
    case 'agent_activity':
      return <AgentActivityCell cell={cell} />;
    case 'error':
      return <ErrorCell cell={cell} />;
    default:
      return null;
  }
}
