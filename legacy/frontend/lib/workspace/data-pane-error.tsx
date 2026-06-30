/**
 * DataPaneError — shared inline error/retry for workspace data panes.
 *
 * Every data pane that fetches workspace state should use this component
 * instead of a raw string banner. It:
 *  - Maps common transport/server/auth errors to a human-readable cause
 *  - Always shows a Retry button that re-runs the caller's fetch
 *  - Keeps the AI-limit / no-fallback messages from prior work intact
 *    (those are surfaced by the chat pane's own sendMessage classifier)
 */

import { AppButton } from '@/lib/ui/primitives';
import { RefreshCw, WifiOff, ShieldAlert, Clock, ServerCrash, AlertTriangle } from 'lucide-react';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isWorkstationClientError(
  error: unknown,
): { status: number; code: string | null } | null {
  if (
    error instanceof Error
    && (error as unknown as Record<string, unknown>).name === 'WorkstationClientError'
  ) {
    const record = error as unknown as Record<string, unknown>;
    const status = typeof record.status === 'number' ? record.status : -1;
    const code = typeof record.code === 'string' ? record.code : null;
    return { status, code };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Cause detection
// ---------------------------------------------------------------------------

export type ErrorCause =
  | 'offline'
  | 'unauthorized'
  | 'forbidden'
  | 'rate-limited'
  | 'server-error'
  | 'not-found'
  | 'timeout'
  | 'generic';

export interface DataPaneErrorInfo {
  cause: ErrorCause;
  message: string;
}

export function classifyDataPaneError(error: unknown): DataPaneErrorInfo {
  const wce = isWorkstationClientError(error);

  // Transport / network
  if (wce && wce.status === 0) {
    return {
      cause: 'offline',
      message: 'Cannot reach the server. Check your connection and retry.',
    };
  }

  if (wce && wce.status === 401) {
    return {
      cause: 'unauthorized',
      message: 'Your session expired. Sign in again and retry.',
    };
  }

  if (wce && wce.status === 403) {
    return {
      cause: 'forbidden',
      message: 'This workspace does not allow that request right now.',
    };
  }

  if (wce && wce.status === 429) {
    return {
      cause: 'rate-limited',
      message: 'Too many requests. Retry in a moment.',
    };
  }

  if (wce && wce.status === 404) {
    return {
      cause: 'not-found',
      message: 'The requested data could not be found.',
    };
  }

  if (wce && wce.status >= 500) {
    return {
      cause: 'server-error',
      message: 'The server hit a temporary issue. Retry when ready.',
    };
  }

  // Timeout
  if (error instanceof Error && error.name === 'TimeoutError') {
    return {
      cause: 'timeout',
      message: 'The request took too long. Retry when ready.',
    };
  }

  // Abort / cancellation
  if (error instanceof DOMException && error.name === 'AbortError') {
    return {
      cause: 'generic',
      message: 'The request was cancelled. Retry when ready.',
    };
  }

  // Network error (TypeError from fetch: "Failed to fetch" / "NetworkError")
  if (error instanceof TypeError && /fetch|network/i.test(error.message)) {
    return {
      cause: 'offline',
      message: 'Cannot reach the server. Check your connection and retry.',
    };
  }

  // Fallback: use error message if available
  const fallbackMessage = error instanceof Error && error.message.trim()
    ? error.message.trim()
    : 'Something went wrong. Retry when ready.';

  return {
    cause: 'generic',
    message: fallbackMessage,
  };
}

// ---------------------------------------------------------------------------
// Icon per cause
// ---------------------------------------------------------------------------

function ErrorCauseIcon({ cause }: { cause: ErrorCause }) {
  const size = 14;
  switch (cause) {
    case 'offline':
      return <WifiOff size={size} aria-hidden="true" />;
    case 'unauthorized':
    case 'forbidden':
      return <ShieldAlert size={size} aria-hidden="true" />;
    case 'rate-limited':
      return <Clock size={size} aria-hidden="true" />;
    case 'server-error':
      return <ServerCrash size={size} aria-hidden="true" />;
    case 'timeout':
      return <Clock size={size} aria-hidden="true" />;
    default:
      return <AlertTriangle size={size} aria-hidden="true" />;
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface DataPaneErrorProps {
  /** The error to classify and display. */
  error: unknown;
  /** Called when the user clicks Retry. The caller re-runs its fetch. */
  onRetry: () => void;
  /** Optional context label, e.g. "History" or "Connections". */
  label?: string;
}

/**
 * Inline error banner with a human-readable cause and a Retry button.
 *
 * Usage:
 * ```tsx
 * <DataPaneError error={loadError} onRetry={() => refresh()} label="History" />
 * ```
 */
export function DataPaneError({ error, onRetry, label }: DataPaneErrorProps) {
  const info = classifyDataPaneError(error);
  const prefix = label ? `${label} could not load. ` : '';

  return (
    <div className="data-pane-error" role="alert">
      <span className="data-pane-error__icon">
        <ErrorCauseIcon cause={info.cause} />
      </span>
      <span className="data-pane-error__text">{prefix}{info.message}</span>
      <AppButton
        type="button"
        tone="ghost"
        className="data-pane-error__retry"
        onClick={onRetry}
      >
        <RefreshCw size={12} aria-hidden="true" />
        Retry
      </AppButton>
    </div>
  );
}

/**
 * Tiny inline variant for sub-fetch failures inside a pane.
 * Use this when a single sub-resource (e.g. approvals, models) fails
 * rather than the entire pane load.
 */
export function DataPaneInlineError({
  error,
  onRetry,
  label,
}: DataPaneErrorProps) {
  const info = classifyDataPaneError(error);
  const prefix = label ? `${label}: ` : '';

  return (
    <span className="data-pane-inline-error" role="alert">
      {prefix}{info.message}{' '}
      <button
        type="button"
        className="data-pane-inline-error__retry"
        onClick={onRetry}
      >
        Retry
      </button>
    </span>
  );
}
