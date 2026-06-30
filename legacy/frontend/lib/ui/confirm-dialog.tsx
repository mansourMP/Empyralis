'use client';

import type { ReactNode } from 'react';

import { AppButton } from '@/lib/ui/primitives';
import { Modal } from '@/lib/ui/modal';

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  confirmTone = 'danger',
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: ReactNode;
  body: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmTone?: 'primary' | 'secondary' | 'danger';
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal
      open={open}
      title={title}
      description={body}
      onClose={onCancel}
      size="small"
      actions={(
        <>
          <AppButton type="button" tone="secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </AppButton>
          <AppButton type="button" tone={confirmTone} onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : confirmLabel}
          </AppButton>
        </>
      )}
    />
  );
}
