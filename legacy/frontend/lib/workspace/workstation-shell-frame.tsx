import type { PropsWithChildren, ReactNode } from 'react';

import Link from 'next/link';

import { ScrollRegion } from '@/lib/ui/scroll-region';

function humanizeSurface(surface: string): string {
  return surface
    .split('/')
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(' / ');
}

export function WorkstationShellFrame({
  switcherPane,
  kernelPane,
}: {
  switcherPane: ReactNode;
  kernelPane: ReactNode;
}) {
  void switcherPane;

  return (
    <div
      data-workstation-shell="frame"
      data-workstation-shell-zones="1"
      className="app-shell-frame"
    >
      <div
        data-workstation-shell="kernel-host"
        data-workstation-shell-zone="content"
        className="app-shell-frame__kernel"
      >
        {kernelPane}
      </div>
    </div>
  );
}

export function WorkstationSurfaceViewport({
  children,
  surface,
}: PropsWithChildren<{
  surface?: string;
}>) {
  if (surface === 'chat' || surface === 'memory') {
    return (
      <div
        data-workstation-surface="viewport"
        data-workstation-surface-view={surface}
        className="app-shell-viewport app-shell-viewport--locked"
      >
        {children}
      </div>
    );
  }

  return (
    <ScrollRegion
      data-workstation-surface="viewport"
      data-workstation-surface-view={surface ?? 'unknown'}
      className="app-shell-viewport"
    >
      {children}
    </ScrollRegion>
  );
}

export function WorkstationRouteFallback({
  surface,
  fallbackHref,
}: {
  surface: string;
  shellProfileId: string;
  fallbackHref: string;
}) {
  return (
    <div data-workstation-surface="fallback" className="app-route-fallback">
      <div className="app-route-fallback__header">
        <span className="app-data-badge app-data-badge--accent app-route-fallback__eyebrow">Unavailable</span>
        <h1 className="app-route-fallback__title">This view is not available here</h1>
      </div>

      <p className="app-route-fallback__body">
        {humanizeSurface(surface)} is not enabled for the current workspace configuration.
      </p>

      <div>
        <Link href={fallbackHref} className="app-link-button app-link-button--primary">
          Open workspace default view
        </Link>
      </div>
    </div>
  );
}
