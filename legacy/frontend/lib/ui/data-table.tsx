'use client';

import type { HTMLAttributes, PropsWithChildren, ReactNode } from 'react';

import { joinClassNames } from '@/lib/ui/primitives';

type DataTone = 'neutral' | 'success' | 'warning' | 'danger' | 'accent';

function badgePalette(tone: DataTone): { color: string; border: string; background: string } {
  return {
    color: `app-data-badge--${tone}`,
    border: '',
    background: '',
  };
}

function resolveColumnsVariant(columns: string): string {
  if (columns === 'minmax(0, 1.25fr) minmax(0, 0.6fr) minmax(0, 0.7fr) minmax(0, 0.9fr) auto') {
    return 'app-data-table-grid--runs';
  }
  if (columns === 'minmax(0, 1.25fr) minmax(0, 0.6fr) minmax(0, 0.8fr) auto') {
    return 'app-data-table-grid--approvals';
  }
  if (columns === 'minmax(0, 1.2fr) minmax(0, 0.7fr) minmax(0, 0.7fr) auto') {
    return 'app-data-table-grid--artifacts';
  }
  if (columns === 'minmax(0, 1.2fr) minmax(0, 0.6fr) minmax(0, 0.8fr)') {
    return 'app-data-table-grid--overview-approvals';
  }
  if (columns === 'minmax(0, 1fr) minmax(0, 0.7fr) minmax(0, 0.8fr)') {
    return 'app-data-table-grid--overview-runs';
  }
  if (columns === 'minmax(0, 2fr) minmax(8rem, 0.9fr) minmax(13rem, 1.1fr)') {
    return 'app-data-table-grid--members';
  }
  if (columns === 'minmax(0, 2fr) minmax(8rem, 0.9fr) minmax(12rem, 1fr)') {
    return 'app-data-table-grid--invites';
  }
  if (columns === 'minmax(0, 2fr) minmax(8rem, 0.9fr) minmax(10rem, 0.9fr)') {
    return 'app-data-table-grid--history';
  }
  if (columns === 'minmax(0, 1.5fr) minmax(9rem, 1fr) minmax(9rem, 0.8fr)') {
    return 'app-data-table-grid--routing';
  }
  return 'app-data-table-grid--default';
}

export function DataTable({
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div {...props} className={joinClassNames('app-data-table', className)}>
      {children}
    </div>
  );
}

export function DataTableHeader({
  columns,
  children,
}: PropsWithChildren<{
  columns: string;
}>) {
  return (
    <div className={joinClassNames('app-data-table__header', resolveColumnsVariant(columns))}>
      {children}
    </div>
  );
}

export function DataTableHeaderCell({
  children,
  align = 'start',
}: PropsWithChildren<{
  align?: 'start' | 'center' | 'end';
}>) {
  return (
    <div className={joinClassNames('app-data-table__header-cell', `app-data-table__header-cell--${align}`)}>
      {children}
    </div>
  );
}

export function DataTableRow({
  columns,
  selected = false,
  onClick,
  children,
}: PropsWithChildren<{
  columns: string;
  selected?: boolean;
  onClick?: () => void;
}>) {
  const classes = joinClassNames(
    'app-data-table__row',
    resolveColumnsVariant(columns),
    selected && 'app-data-table__row--selected',
    onClick && 'app-data-table__row--interactive',
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={classes}
      >
        {children}
      </button>
    );
  }

  return <div className={classes}>{children}</div>;
}

export function DataTableCell({
  primary,
  secondary,
  meta,
  align = 'start',
  mono = false,
}: {
  primary: ReactNode;
  secondary?: ReactNode;
  meta?: ReactNode;
  align?: 'start' | 'center' | 'end';
  mono?: boolean;
}) {
  return (
    <div className={joinClassNames('app-data-table__cell', `app-data-table__cell--${align}`)}>
      <div
        className={joinClassNames(
          'app-data-table__cell-primary',
          mono && 'app-data-table__cell-primary--mono',
        )}
      >
        {primary}
      </div>
      {secondary ? (
        <div className="app-data-table__cell-secondary">{secondary}</div>
      ) : null}
      {meta ? (
        <div className="app-data-table__cell-meta">{meta}</div>
      ) : null}
    </div>
  );
}

export function DataBadge({
  tone = 'neutral',
  children,
}: PropsWithChildren<{
  tone?: DataTone;
}>) {
  const palette = badgePalette(tone);
  return (
    <span className={joinClassNames('app-data-badge', palette.color)}>
      {children}
    </span>
  );
}
