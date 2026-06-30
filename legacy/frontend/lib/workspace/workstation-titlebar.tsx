'use client';

import type { ReactNode } from 'react';
import Link from 'next/link';

import { WorkstationAppUpdateAction } from '@/lib/workspace/workstation-app-update-action';
import { DesktopWindowControls } from '@/lib/workspace/desktop-window-controls';
import { useWorkstationDesktopBridge } from '@/lib/workspace/workstation-desktop-bridge';

export function WorkstationTitlebar({
  surfaceLabel,
  surfaceHref,
  surfaceControl,
  diagnosticsVisible: _diagnosticsVisible,
  onToggleDiagnostics: _onToggleDiagnostics,
  navigation,
  actions,
  leftAction,
}: {
  surfaceLabel: string;
  surfaceHref?: string;
  surfaceControl?: ReactNode;
  diagnosticsVisible: boolean;
  onToggleDiagnostics: () => void;
  navigation?: ReactNode;
  actions?: ReactNode;
  leftAction?: ReactNode;
}) {
  const desktop = useWorkstationDesktopBridge();

  return (
    <header
      className="workstation-titlebar"
      data-workstation-titlebar="root"
      onDoubleClick={() => {
        if (desktop.available) {
          void desktop.window.toggleMaximize();
        }
      }}
    >
      <div
        className="workstation-titlebar__brand"
        data-tauri-drag-region={desktop.available ? '' : undefined}
      >
        {leftAction}
        {surfaceControl ?? (surfaceHref ? (
          <Link
            href={surfaceHref}
            className="workstation-titlebar__surface-link"
            aria-label={`Open ${surfaceLabel}`}
          >
            <span className="workstation-titlebar__surface">{surfaceLabel}</span>
          </Link>
        ) : (
          <span className="workstation-titlebar__surface">{surfaceLabel}</span>
        ))}
        <div className="workstation-titlebar__brand-actions" id="workstation-titlebar-brand-actions-slot" />
      </div>
      {navigation ? (
        <nav className="workstation-titlebar__nav" aria-label="Workspace views">
          {navigation}
        </nav>
      ) : (
        <div className="workstation-titlebar__spacer" data-tauri-drag-region={desktop.available ? '' : undefined} />
      )}
      <div className="workstation-titlebar__right">
        <div className="workstation-titlebar__actions" id="workstation-titlebar-actions-slot">
          <WorkstationAppUpdateAction />
          {actions}
        </div>
        <DesktopWindowControls />
      </div>
    </header>
  );
}
