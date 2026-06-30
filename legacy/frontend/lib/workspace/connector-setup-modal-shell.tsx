'use client';

import type { ReactNode } from 'react';

import { CommandSheet } from '@/lib/ui/command-sheet';

export function ConnectorSetupModalShell({
  open,
  title,
  description,
  onClose,
  children,
  actions,
  className,
}: {
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <CommandSheet
      open={open}
      title={title}
      description={description}
      onClose={onClose}
      actions={actions}
      className={className}
    >
      {children}
    </CommandSheet>
  );
}
