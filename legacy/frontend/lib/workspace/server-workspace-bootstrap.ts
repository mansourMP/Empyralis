import 'server-only';

import { headers } from 'next/headers';

import { controlPlaneBaseUrl } from '@/lib/server/control-plane-base-url';
import {
  type WorkspaceBootstrapPayload,
  parseWorkspaceBootstrapPayload,
} from '@/lib/workspace/workspace-bootstrap';

export class WorkspaceBootstrapError extends Error {
  status: number;
  workspaceId: string;

  constructor(workspaceId: string, status: number, message?: string) {
    super(
      message ?? `Workspace bootstrap request failed for ${workspaceId} with status ${status}.`,
    );
    this.name = 'WorkspaceBootstrapError';
    this.status = status;
    this.workspaceId = workspaceId;
  }
}

const TRANSIENT_BOOTSTRAP_STATUSES = new Set([429, 500, 502, 503, 504]);
const WORKSPACE_BOOTSTRAP_CACHE_TTL_MS = 30_000;
const WORKSPACE_BOOTSTRAP_STALE_TTL_MS = 5 * 60_000;
const WORKSPACE_BOOTSTRAP_TIMEOUT_MS = 12_000;

type WorkspaceBootstrapCacheEntry = {
  expiresAt: number;
  payload: WorkspaceBootstrapPayload;
  staleUntil: number;
};

const workspaceBootstrapCache = new Map<string, WorkspaceBootstrapCacheEntry>();
const workspaceBootstrapInFlightRequests = new Map<string, Promise<WorkspaceBootstrapPayload>>();

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function canUseStaleWorkspaceBootstrap(
  cachedEntry: WorkspaceBootstrapCacheEntry | undefined,
  error: unknown,
): cachedEntry is WorkspaceBootstrapCacheEntry {
  return Boolean(
    cachedEntry
    && cachedEntry.staleUntil > Date.now()
    && error instanceof WorkspaceBootstrapError
    && TRANSIENT_BOOTSTRAP_STATUSES.has(error.status),
  );
}

export async function loadWorkspaceBootstrap(workspaceId: string): Promise<WorkspaceBootstrapPayload> {
  const requestHeaders = await headers();
  const url = `${controlPlaneBaseUrl()}/api/workspaces/${encodeURIComponent(workspaceId)}/bootstrap`;
  const host = requestHeaders.get('x-forwarded-host') ?? requestHeaders.get('host');
  const proto = requestHeaders.get('x-forwarded-proto') ?? 'http';
  const forwardHeaders: Record<string, string> = {
    accept: 'application/json',
  };
  const cookie = requestHeaders.get('cookie');
  const authorization = requestHeaders.get('authorization');
  if (cookie) {
    forwardHeaders.cookie = cookie;
  }
  if (authorization) {
    forwardHeaders.authorization = authorization;
  }
  if (host) {
    forwardHeaders['x-forwarded-host'] = host.split(',')[0].trim();
  }
  if (proto) {
    forwardHeaders['x-forwarded-proto'] = proto.split(',')[0].trim() || 'http';
  }
  const cacheKey = [
    workspaceId,
    cookie ?? '',
    authorization ?? '',
    forwardHeaders['x-forwarded-host'] ?? '',
    forwardHeaders['x-forwarded-proto'] ?? '',
  ].join('::');
  const cachedEntry = workspaceBootstrapCache.get(cacheKey);
  if (cachedEntry && cachedEntry.expiresAt > Date.now()) {
    return cachedEntry.payload;
  }
  const existingRequest = workspaceBootstrapInFlightRequests.get(cacheKey);
  if (existingRequest) {
    try {
      return await existingRequest;
    } catch (error) {
      if (canUseStaleWorkspaceBootstrap(cachedEntry, error)) {
        return cachedEntry.payload;
      }
      throw error;
    }
  }

  const requestPromise = fetchWorkspaceBootstrapWithRetry({
    workspaceId,
    url,
    forwardHeaders,
    cacheKey,
  });
  workspaceBootstrapInFlightRequests.set(cacheKey, requestPromise);
  try {
    return await requestPromise;
  } catch (error) {
    if (canUseStaleWorkspaceBootstrap(cachedEntry, error)) {
      return cachedEntry.payload;
    }
    throw error;
  } finally {
    if (workspaceBootstrapInFlightRequests.get(cacheKey) === requestPromise) {
      workspaceBootstrapInFlightRequests.delete(cacheKey);
    }
  }
}

async function fetchWorkspaceBootstrapWithRetry({
  workspaceId,
  url,
  forwardHeaders,
  cacheKey,
}: {
  workspaceId: string;
  url: string;
  forwardHeaders: Record<string, string>;
  cacheKey: string;
}): Promise<WorkspaceBootstrapPayload> {
  let lastStatus = 500;
  let lastError: unknown = null;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const controller = new AbortController();
      const timeoutHandle = setTimeout(() => {
        controller.abort();
      }, WORKSPACE_BOOTSTRAP_TIMEOUT_MS);
      let response: Response;
      try {
        response = await fetch(url, {
          method: 'GET',
          cache: 'no-store',
          headers: forwardHeaders,
          signal: controller.signal,
        });
      } finally {
        clearTimeout(timeoutHandle);
      }

      if (response.ok) {
        const payload = await response.json();
        const parsedPayload = parseWorkspaceBootstrapPayload(payload);
        workspaceBootstrapCache.set(cacheKey, {
          expiresAt: Date.now() + WORKSPACE_BOOTSTRAP_CACHE_TTL_MS,
          payload: parsedPayload,
          staleUntil: Date.now() + WORKSPACE_BOOTSTRAP_STALE_TTL_MS,
        });
        return parsedPayload;
      }

      lastStatus = response.status;
      if (!TRANSIENT_BOOTSTRAP_STATUSES.has(response.status) || attempt === 2) {
        throw new WorkspaceBootstrapError(workspaceId, response.status);
      }
    } catch (error) {
      lastError = error;
      if (error instanceof Error && error.name === 'AbortError') {
        lastStatus = 504;
      }
      if (error instanceof WorkspaceBootstrapError && !TRANSIENT_BOOTSTRAP_STATUSES.has(error.status)) {
        throw error;
      }
      if (attempt === 2) {
        break;
      }
    }
    await delay(150 * (attempt + 1));
  }
  if (lastError instanceof WorkspaceBootstrapError) {
    throw lastError;
  }
  throw new WorkspaceBootstrapError(
    workspaceId,
    lastStatus,
    lastError instanceof Error && lastError.name === 'AbortError'
      ? `Workspace bootstrap timed out after ${WORKSPACE_BOOTSTRAP_TIMEOUT_MS}ms.`
      : lastError instanceof Error ? lastError.message : undefined,
  );
}
