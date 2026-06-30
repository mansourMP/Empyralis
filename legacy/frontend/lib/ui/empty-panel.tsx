'use client';

import type { HTMLAttributes, ReactNode } from 'react';

import { joinClassNames } from '@/lib/ui/primitives';

export function EmptyPanel({
  title,
  body,
  actions,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  title: ReactNode;
  body: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div
      {...props}
      className={joinClassNames('app-empty-panel', className)}
    >
      <strong className="app-empty-panel__title">{title}</strong>
      <div className="app-empty-panel__body">
        {body}
      </div>
      {actions}
    </div>
  );
}
