'use client';

import type { ReactNode } from 'react';

import { joinClassNames } from '@/lib/ui/primitives';
import { useWorkstationDesktopBridge } from '@/lib/workspace/workstation-desktop-bridge';

function WindowControlIcon({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <svg
      viewBox="0 0 12 12"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

function MacTrafficLight({
  tone,
  label,
  onClick,
}: {
  tone: 'close' | 'minimize' | 'maximize';
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      className={joinClassNames(
        'desktop-window-controls__traffic-light',
        `desktop-window-controls__traffic-light--${tone}`,
      )}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    />
  );
}

function AppWindowButton({
  label,
  onClick,
  children,
  tone = 'neutral',
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
  tone?: 'neutral' | 'danger';
}) {
  return (
    <button
      type="button"
      aria-label={label}
      className={joinClassNames(
        'desktop-window-controls__button',
        tone === 'danger' && 'desktop-window-controls__button--danger',
      )}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      {children}
    </button>
  );
}

export function DesktopWindowControls() {
  const desktop = useWorkstationDesktopBridge();

  if (!desktop.available || !desktop.window.supported) {
    return null;
  }

  if (desktop.platform === 'macos') {
    return (
      <div className="desktop-window-controls desktop-window-controls--macos">
        <MacTrafficLight
          tone="close"
          label="Close window"
          onClick={() => {
            void desktop.window.close();
          }}
        />
        <MacTrafficLight
          tone="minimize"
          label="Minimize window"
          onClick={() => {
            void desktop.window.minimize();
          }}
        />
        <MacTrafficLight
          tone="maximize"
          label={desktop.window.maximized ? 'Restore window' : 'Maximize window'}
          onClick={() => {
            void desktop.window.toggleMaximize();
          }}
        />
      </div>
    );
  }

  return (
    <div className="desktop-window-controls">
      <AppWindowButton
        label="Minimize window"
        onClick={() => {
          void desktop.window.minimize();
        }}
      >
        <WindowControlIcon>
          <path d="M2 8.5h8" />
        </WindowControlIcon>
      </AppWindowButton>
      <AppWindowButton
        label={desktop.window.maximized ? 'Restore window' : 'Maximize window'}
        onClick={() => {
          void desktop.window.toggleMaximize();
        }}
      >
        <WindowControlIcon>
          {desktop.window.maximized ? (
            <>
              <path d="M3.5 4.5h5v5h-5z" />
              <path d="M2.5 7.5v-4h4" />
            </>
          ) : (
            <path d="M3 3.5h6v5h-6z" />
          )}
        </WindowControlIcon>
      </AppWindowButton>
      <AppWindowButton
        label="Close window"
        tone="danger"
        onClick={() => {
          void desktop.window.close();
        }}
      >
        <WindowControlIcon>
          <path d="M3.5 3.5 8.5 8.5" />
          <path d="M8.5 3.5 3.5 8.5" />
        </WindowControlIcon>
      </AppWindowButton>
    </div>
  );
}
