'use client';

import {
  WorkstationClientError,
} from '@/lib/workspace/workstation-client';
import type {
  WorkstationSageProfileRecord,
} from '@/lib/workspace/workstation-client';

import type { SageMemorySnapshot, SageProfileSnapshot, SageSetupSurfaceState } from '@/lib/workspace/workstation-chat-pane-hooks';

type TimeoutRunner = <T>(
  operation: Promise<T>,
  timeoutMs: number,
  timeoutMessage: string,
) => Promise<T>;

type ShouldUseFallback = (error: unknown) => boolean;
type SageSetupFailureMessage = (error: unknown, phase: 'load' | 'save') => string;

type LoadMemoryOptions = {
  services: {
    client: {
      listSageMemory: () => Promise<unknown>;
    };
    queryClient: {
      run<T>(key: string, runner: () => Promise<T>): Promise<T>;
      peek<T>(key: string): T | null | undefined;
    };
  };
  memoryQueryKey: string;
  timeoutMs: number;
  cachedSnapshot: SageMemorySnapshot;
  normalizeSnapshot: (payload: unknown) => SageMemorySnapshot;
  writeSnapshot: (snapshot: SageMemorySnapshot) => void;
  withTimeout: TimeoutRunner;
  shouldUseFallback: ShouldUseFallback;
};

export async function loadChatMemorySnapshot(options: LoadMemoryOptions): Promise<SageMemorySnapshot> {
  const payload = await options.withTimeout(
    options.services.queryClient.run('chat:canonical:memory', async () =>
      options.services.client.listSageMemory().catch((error: unknown) => {
        if (options.shouldUseFallback(error)) {
          return null;
        }
        throw error;
      }),
    ),
    options.timeoutMs,
    'Memory took too long to load.',
  );
  const nextSnapshot = payload === null
    ? options.cachedSnapshot
    : options.normalizeSnapshot(payload);
  options.writeSnapshot(nextSnapshot);
  return nextSnapshot;
}

type LoadProfileOptions = {
  services: {
    client: {
      getSageProfile: () => Promise<WorkstationSageProfileRecord>;
    };
    queryClient: {
      run<T>(key: string, runner: () => Promise<T>): Promise<T>;
    };
  };
  timeoutMs: number;
  cachedProfile: SageProfileSnapshot;
  normalizeSnapshot: (payload: WorkstationSageProfileRecord | null) => SageProfileSnapshot;
  writeSnapshot: (snapshot: SageProfileSnapshot) => void;
  setSetupState: (state: SageSetupSurfaceState) => void;
  setSetupMessage: (message: string | null) => void;
  humanizeFailure: SageSetupFailureMessage;
  withTimeout: TimeoutRunner;
  shouldUseFallback: ShouldUseFallback;
};

export async function loadChatProfileSnapshot(options: LoadProfileOptions): Promise<SageProfileSnapshot> {
  let profileError: unknown = null;
  let payload: WorkstationSageProfileRecord | null = null;

  try {
    payload = await options.withTimeout(
      options.services.queryClient.run('chat:canonical:sage-profile', async () =>
        options.services.client.getSageProfile().catch((error: unknown) => {
          if (options.shouldUseFallback(error)) {
            return null;
          }
          profileError = error;
          return null;
        }),
      ),
      options.timeoutMs,
      'Sage setup timed out.',
    );
  } catch (error) {
    profileError = error;
  }

  if (profileError) {
    options.setSetupState('unavailable');
    options.setSetupMessage(options.humanizeFailure(profileError, 'load'));
    return options.cachedProfile;
  }

  const nextProfile = options.normalizeSnapshot(payload);
  options.writeSnapshot(nextProfile);
  return nextProfile;
}

export function isApprovalsPlanError(error: unknown): boolean {
  if (!(error instanceof WorkstationClientError)) {
    return false;
  }
  if (error.status !== 403) {
    return false;
  }
  if (typeof error.detail === 'string') {
    return error.detail.includes('Approvals are not included in this workspace plan.');
  }
  if (!error.detail || typeof error.detail !== 'object') {
    return false;
  }
  const message = (error.detail as { message?: unknown }).message;
  return typeof message === 'string' && message.includes('Approvals are not included in this workspace plan.');
}
