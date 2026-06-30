'use client';

import { useEffect, useState } from 'react';

import {
  useWorkstationDesktopBridge,
  type WorkstationDesktopAppUpdateState,
} from '@/lib/workspace/workstation-desktop-bridge';

function describeUpdate(state: WorkstationDesktopAppUpdateState | null, fallbackError: string | null): string | undefined {
  if (fallbackError) {
    return fallbackError;
  }
  const parts = [state?.detail, state?.body].filter((value): value is string => typeof value === 'string' && value.trim().length > 0);
  return parts.length > 0 ? parts.join('\n\n') : undefined;
}

export function WorkstationAppUpdateAction() {
  const desktop = useWorkstationDesktopBridge();
  const [updateState, setUpdateState] = useState<WorkstationDesktopAppUpdateState | null>(null);
  const [installing, setInstalling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!desktop.available) {
      setUpdateState(null);
      return;
    }

    let active = true;
    void desktop.updates.check()
      .then((nextState) => {
        if (active) {
          setUpdateState(nextState);
        }
      })
      .catch(() => {
        if (active) {
          setUpdateState(null);
        }
      });
    return () => {
      active = false;
    };
  }, [desktop.available]);

  if (!desktop.available) {
    return null;
  }

  const available = Boolean(updateState?.available);
  if (!available && !actionError) {
    return null;
  }

  const label = installing
    ? 'Updating…'
    : actionError
      ? 'Retry update'
      : updateState?.version
        ? `Update ${updateState.version}`
        : 'Update ready';

  return (
    <button
      type="button"
      className="workstation-titlebar__action workstation-titlebar__action--label workstation-titlebar__action--update"
      disabled={installing}
      title={describeUpdate(updateState, actionError)}
      onClick={() => {
        if (installing) {
          return;
        }
        setActionError(null);
        setInstalling(true);
        void desktop.updates.install()
          .then((nextState) => {
            setUpdateState(nextState);
          })
          .catch((error) => {
            setActionError(error instanceof Error ? error.message : 'Update install failed.');
          })
          .finally(() => {
            setInstalling(false);
          });
      }}
    >
      <span>{label}</span>
    </button>
  );
}
