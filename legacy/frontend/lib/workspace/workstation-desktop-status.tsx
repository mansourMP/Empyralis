'use client';

import { useCallback } from 'react';

import { AppButton } from '@/lib/ui/primitives';
import { useWorkstationDesktopBridge } from '@/lib/workspace/workstation-desktop-bridge';

export function WorkstationDesktopStatus() {
  const desktop = useWorkstationDesktopBridge();

  const handleOpenPermissions = useCallback(() => {
    void desktop.openPermissionSettings('accessibility');
  }, [desktop]);

  if (!desktop.available) {
    return null;
  }

  const localCompanionLabel = desktop.localCompanion.present
    ? `${desktop.localCompanion.label ?? 'This Device'} ${desktop.localCompanion.online ? 'ready' : 'needs attention'}`
    : 'No device attached to this workspace';

  return (
    <section
      data-workstation-desktop-bridge="native"
      className="workstation-desktop-status"
    >
      <div className="workstation-desktop-status__summary">
        <span className="workstation-desktop-status__badge">
          Native shell{desktop.platform ? ` · ${desktop.platform}` : ''}
        </span>
        <span
          className={`workstation-desktop-status__detail${desktop.localCompanion.online ? ' workstation-desktop-status__detail--ready' : ''}`}
        >
          {localCompanionLabel}
        </span>
      </div>

      {desktop.localCompanion.present && !desktop.localCompanion.online ? (
        <AppButton type="button" tone="secondary" onClick={handleOpenPermissions}>
          Review computer permissions
        </AppButton>
      ) : null}
    </section>
  );
}
