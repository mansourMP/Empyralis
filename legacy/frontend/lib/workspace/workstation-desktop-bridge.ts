'use client';

import { useEffect, useMemo, useState } from 'react';

import { useWorkspaceBoundary } from '@/lib/workspace/workspace-boundary';

type RawDesktopWindowState = {
  maximized?: boolean;
};

export type WorkstationDesktopAppUpdateState = {
  configured: boolean;
  available: boolean;
  current_version?: string;
  version?: string | null;
  body?: string | null;
  state?: string | null;
  detail?: string | null;
};

type RawEmpyralisDesktopBridge = {
  desktop?: boolean;
  platform?: string;
  openExternal?: (target: string) => Promise<boolean>;
  markShellReady?: () => Promise<boolean>;
  getWindowState?: () => Promise<RawDesktopWindowState>;
  minimizeWindow?: () => Promise<boolean>;
  toggleMaximizeWindow?: () => Promise<RawDesktopWindowState>;
  closeWindow?: () => Promise<boolean>;
  startWindowDrag?: () => Promise<boolean>;
  openPermissionSettings?: (permission: string) => Promise<boolean>;
  bootstrapMachineEnrollment?: (intent: unknown) => Promise<unknown>;
  openaiCodexOauthLogin?: () => Promise<unknown>;
  checkAppUpdate?: () => Promise<WorkstationDesktopAppUpdateState | null>;
  installAppUpdate?: () => Promise<WorkstationDesktopAppUpdateState | null>;
};

type DesktopWindowLike = Window & {
  empyralisDesktop?: RawEmpyralisDesktopBridge;
  orionDesktop?: RawEmpyralisDesktopBridge;
};

export type WorkstationDesktopLocalCompanionState = {
  present: boolean;
  label: string | null;
  online: boolean;
  preferred: boolean;
};

export type WorkstationDesktopWindowState = {
  maximized: boolean;
};

export type WorkstationDesktopBridgeState = {
  available: boolean;
  platform: string | null;
  localCompanion: WorkstationDesktopLocalCompanionState;
  nativeChrome: boolean;
  shellReady: () => Promise<boolean>;
  window: {
    supported: boolean;
    maximized: boolean;
    refresh: () => Promise<WorkstationDesktopWindowState>;
    minimize: () => Promise<boolean>;
    toggleMaximize: () => Promise<WorkstationDesktopWindowState>;
    close: () => Promise<boolean>;
    startDrag: () => Promise<boolean>;
  };
  openExternal: (target: string) => Promise<boolean>;
  openPermissionSettings: (permission: 'screen_recording' | 'accessibility' | 'filesystem') => Promise<boolean>;
  updates: {
    check: () => Promise<WorkstationDesktopAppUpdateState | null>;
    install: () => Promise<WorkstationDesktopAppUpdateState | null>;
  };
};

export function resolveWorkstationDesktopBridge(
  targetWindow?: DesktopWindowLike | null,
): RawEmpyralisDesktopBridge | null {
  const candidateWindow = targetWindow ?? (typeof window !== 'undefined' ? window as DesktopWindowLike : null);
  if (!candidateWindow) {
    return null;
  }

  const bridge = candidateWindow.empyralisDesktop ?? candidateWindow.orionDesktop ?? null;
  if (!bridge || bridge.desktop !== true) {
    return null;
  }

  return bridge;
}

function isLocalCompanionTarget(target: { id: string; kind: string }): boolean {
  const id = String(target.id || '').trim().toLowerCase();
  const kind = String(target.kind || '').trim().toLowerCase();
  return id === 'local_companion' || kind === 'local_companion';
}

function normalizeWindowState(value: RawDesktopWindowState | null | undefined): WorkstationDesktopWindowState {
  return {
    maximized: Boolean(value?.maximized),
  };
}

export function useWorkstationDesktopBridge(): WorkstationDesktopBridgeState {
  const { bootstrap } = useWorkspaceBoundary();
  const [windowState, setWindowState] = useState<WorkstationDesktopWindowState>({ maximized: false });

  const desktopBridge = useMemo(() => resolveWorkstationDesktopBridge(), []);

  useEffect(() => {
    if (!desktopBridge || typeof desktopBridge.getWindowState !== 'function') {
      return undefined;
    }

    let active = true;
    const refresh = async () => {
      try {
        const next = normalizeWindowState(await desktopBridge.getWindowState?.());
        if (active) {
          setWindowState(next);
        }
      } catch {
        if (active) {
          setWindowState({ maximized: false });
        }
      }
    };

    void refresh();
    const onResize = () => {
      void refresh();
    };
    window.addEventListener('resize', onResize);
    return () => {
      active = false;
      window.removeEventListener('resize', onResize);
    };
  }, [desktopBridge]);

  return useMemo(() => {
    const localCompanionTarget = bootstrap.runtime.runtimeTargets.find((target) =>
      isLocalCompanionTarget(target),
    );

    const refreshWindowState = async () => {
      if (!desktopBridge || typeof desktopBridge.getWindowState !== 'function') {
        const fallback = { maximized: false };
        setWindowState(fallback);
        return fallback;
      }
      const next = normalizeWindowState(await desktopBridge.getWindowState());
      setWindowState(next);
      return next;
    };

    return {
      available: Boolean(desktopBridge),
      platform: typeof desktopBridge?.platform === 'string' ? desktopBridge.platform : null,
      nativeChrome: Boolean(desktopBridge),
      localCompanion: {
        present: Boolean(localCompanionTarget),
        label: localCompanionTarget?.label ?? null,
        online: Boolean(localCompanionTarget?.online),
        preferred: Boolean(localCompanionTarget?.preferred),
      },
      shellReady: async () => {
        if (!desktopBridge || typeof desktopBridge.markShellReady !== 'function') {
          return false;
        }
        return Boolean(await desktopBridge.markShellReady());
      },
      window: {
        supported: Boolean(desktopBridge),
        maximized: windowState.maximized,
        refresh: refreshWindowState,
        minimize: async () => {
          if (!desktopBridge || typeof desktopBridge.minimizeWindow !== 'function') {
            return false;
          }
          return Boolean(await desktopBridge.minimizeWindow());
        },
        toggleMaximize: async () => {
          if (!desktopBridge || typeof desktopBridge.toggleMaximizeWindow !== 'function') {
            const fallback = { maximized: false };
            setWindowState(fallback);
            return fallback;
          }
          const next = normalizeWindowState(await desktopBridge.toggleMaximizeWindow());
          setWindowState(next);
          return next;
        },
        close: async () => {
          if (!desktopBridge || typeof desktopBridge.closeWindow !== 'function') {
            return false;
          }
          return Boolean(await desktopBridge.closeWindow());
        },
        startDrag: async () => {
          if (!desktopBridge || typeof desktopBridge.startWindowDrag !== 'function') {
            return false;
          }
          return Boolean(await desktopBridge.startWindowDrag());
        },
      },
      openExternal: async (target: string) => {
        const normalized = target.trim();
        if (!normalized) {
          return false;
        }

        if (desktopBridge && typeof desktopBridge.openExternal === 'function') {
          return Boolean(await desktopBridge.openExternal(normalized));
        }

        if (typeof window !== 'undefined' && /^https?:\/\//.test(normalized)) {
          window.open(normalized, '_blank', 'noopener,noreferrer');
          return true;
        }

        return false;
      },
      openPermissionSettings: async (permission) => {
        if (!desktopBridge || typeof desktopBridge.openPermissionSettings !== 'function') {
          return false;
        }
        return Boolean(await desktopBridge.openPermissionSettings(permission));
      },
      updates: {
        check: async () => {
          if (!desktopBridge || typeof desktopBridge.checkAppUpdate !== 'function') {
            return null;
          }
          return await desktopBridge.checkAppUpdate();
        },
        install: async () => {
          if (!desktopBridge || typeof desktopBridge.installAppUpdate !== 'function') {
            return null;
          }
          return await desktopBridge.installAppUpdate();
        },
      },
    };
  }, [bootstrap.runtime.runtimeTargets, desktopBridge, windowState.maximized]);
}
