'use client';

import { useEffect, useState } from 'react';

import { AppButton } from '@/lib/ui/primitives';
import { SparkIcon } from '@/lib/ui/icons';
import { resolveWorkstationDesktopBridge } from '@/lib/workspace/workstation-desktop-bridge';

type StartupPhase = 'probing' | 'launching' | 'ready' | 'error';

export function DesktopStartupScreen({
  workspaceLabel,
}: {
  workspaceLabel: string;
}) {
  const [desktop, setDesktop] = useState(false);
  const [phase, setPhase] = useState<StartupPhase>('probing');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const bridge = resolveWorkstationDesktopBridge();
    if (!bridge) {
      setDesktop(false);
      setPhase('ready');
      return undefined;
    }

    setDesktop(true);
    setPhase('launching');
    setError(null);

    void (async () => {
      try {
        if (typeof bridge.markShellReady === 'function') {
          await bridge.markShellReady();
        }
        if (!active) {
          return;
        }
        window.setTimeout(() => {
          if (active) {
            setPhase('ready');
          }
        }, 360);
      } catch (launchError) {
        if (!active) {
          return;
        }
        setError(launchError instanceof Error ? launchError.message : 'Desktop shell could not finish startup.');
        setPhase('error');
      }
    })();

    return () => {
      active = false;
    };
  }, []);

  if (!desktop || phase === 'ready') {
    return null;
  }

  return (
    <div
      data-desktop-startup-screen="overlay"
      className="desktop-startup-screen"
    >
      <div className="desktop-startup-screen__card">
        <div className="desktop-startup-screen__copy">
          <span className="desktop-startup-screen__eyebrow">
            <SparkIcon size={15} />
            Empyralis Desktop
          </span>
          <strong className="desktop-startup-screen__title">
            {phase === 'error' ? 'Desktop launch needs attention' : 'Opening workstation'}
          </strong>
          <span className="desktop-startup-screen__body">
            {phase === 'error'
              ? error || 'The native shell could not finish startup.'
              : `Restoring ${workspaceLabel} into the native workstation shell.`}
          </span>
        </div>

        {phase !== 'error' ? (
          <div aria-hidden="true" className="desktop-startup-screen__progress">
            <div className="desktop-startup-screen__progress-bar" />
          </div>
        ) : null}

        {phase === 'error' ? (
          <div className="desktop-startup-screen__actions">
            <AppButton
              type="button"
              onClick={() => {
                window.location.reload();
              }}
            >
              Retry launch
            </AppButton>
          </div>
        ) : null}
      </div>
    </div>
  );
}
