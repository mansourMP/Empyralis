'use client';

import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from 'react';

import {
  AppButton,
  AppNotice,
  AppSurfaceCard,
  AppSurfaceList,
  AppSurfaceListItem,
  AppSurfaceRoot,
  AppSurfaceStat,
  AppSurfaceStatGrid,
} from '@/lib/ui/primitives';

export function WorkstationSurfaceRoot({
  surface,
  children,
}: PropsWithChildren<{
  surface: string;
}>) {
  return (
    <AppSurfaceRoot data-workstation-surface={surface}>
      {children}
    </AppSurfaceRoot>
  );
}

export function WorkstationSurfaceCard({
  title,
  description,
  children,
  actions,
}: PropsWithChildren<{
  title: string;
  description?: string;
  actions?: ReactNode;
}>) {
  return (
    <AppSurfaceCard title={title} description={description} actions={actions}>
      {children}
    </AppSurfaceCard>
  );
}

export function WorkstationSurfaceNotice({
  tone = 'neutral',
  children,
}: PropsWithChildren<{
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
}>) {
  return <AppNotice tone={tone}>{children}</AppNotice>;
}

export function WorkstationSurfaceList({
  children,
}: PropsWithChildren) {
  return <AppSurfaceList>{children}</AppSurfaceList>;
}

export function WorkstationSurfaceListItem({
  title,
  subtitle,
  description,
  actions,
}: {
  title: string;
  subtitle?: string | null;
  description?: string | null;
  actions?: ReactNode;
}) {
  return (
    <AppSurfaceListItem
      title={title}
      subtitle={subtitle}
      description={description}
      actions={actions}
    />
  );
}

export function WorkstationSurfaceStatGrid({
  children,
}: PropsWithChildren) {
  return <AppSurfaceStatGrid>{children}</AppSurfaceStatGrid>;
}

export function WorkstationSurfaceStat({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint: string;
}) {
  return <AppSurfaceStat label={label} value={value} hint={hint} />;
}

export function WorkstationActionButton({
  children,
  tone = 'primary',
  ...props
}: PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: 'primary' | 'secondary' | 'danger';
}>) {
  return (
    <AppButton {...props} tone={tone}>
      {children}
    </AppButton>
  );
}
