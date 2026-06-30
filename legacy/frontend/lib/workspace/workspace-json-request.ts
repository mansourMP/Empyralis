'use client';

import type { WorkspaceServices } from '@/lib/workspace/workspace-services';

const HTML_DOCUMENT_PATTERN = /<!doctype html>|<html[\s>]/i;
const RETRYABLE_STATUSES = [408, 425, 429, 500, 502, 503, 504];

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function looksLikeHtmlDocument(value: string): boolean {
  return HTML_DOCUMENT_PATTERN.test(value);
}

function fallbackWorkspaceError(status: number): string {
  if (status === 401) {
    return 'Your session expired. Reload this page or sign in again.';
  }
  if (status === 403) {
    return 'This workspace request is blocked right now.';
  }
  if (status === 404) {
    return 'This workspace route is unavailable right now.';
  }
  if (status >= 500) {
    return 'The workspace service is temporarily unavailable.';
  }
  return 'This workspace request failed.';
}

function extractWorkspaceErrorMessage(status: number, payload: unknown): string {
  const payloadRecord = asRecord(payload);
  const nestedError = asRecord(payloadRecord?.error);
  const candidates = [
    payload,
    payloadRecord?.detail,
    payloadRecord?.message,
    payloadRecord?.error,
    nestedError?.message,
    nestedError?.detail,
  ];

  for (const candidate of candidates) {
    const message = readString(candidate);
    if (!message) {
      continue;
    }
    if (looksLikeHtmlDocument(message)) {
      return fallbackWorkspaceError(status);
    }
    return message;
  }

  return fallbackWorkspaceError(status);
}

export async function requestWorkspaceJson<T>(
  services: WorkspaceServices,
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers ?? {});
  headers.set('accept', 'application/json');
  if (init.body && !headers.has('content-type')) {
    headers.set('content-type', 'application/json');
  }

  const method = String(init.method ?? 'GET').trim().toUpperCase();
  const response = await services.transport.request(path, {
    ...init,
    headers,
  }, {
    timeoutMs: ['GET', 'HEAD'].includes(method) ? 10_000 : 15_000,
    retryCount: ['GET', 'HEAD'].includes(method) ? 1 : 0,
    retryOnStatuses: RETRYABLE_STATUSES,
    refreshSessionOn401: true,
  });

  let text = '';
  try {
    text = await response.text();
  } catch (error) {
    if (init.signal?.aborted || (error instanceof Error && /aborted/i.test(error.message))) {
      throw error;
    }
    throw new Error('The workspace request was interrupted while reading the response.');
  }

  let payload: unknown = null;
  if (text.trim()) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    throw new Error(extractWorkspaceErrorMessage(response.status, payload));
  }

  if (typeof payload === 'string') {
    if (looksLikeHtmlDocument(payload)) {
      throw new Error('Your session expired. Reload this page or sign in again.');
    }
    throw new Error('The workspace returned an unexpected response.');
  }

  return ((payload ?? {}) as T);
}
