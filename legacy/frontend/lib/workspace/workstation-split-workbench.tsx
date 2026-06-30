'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, PropsWithChildren, ReactNode } from 'react';

function joinClassNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

export function WorkstationSplitWorkbench({
  ariaLabel,
  sidebarHeader,
  sidebar,
  sidebarFooter,
  mainHeader,
  children,
  className,
  resizableSidebar = false,
  sidebarResizeStorageKey,
  sidebarDefaultWidth = 330,
  sidebarMinWidth = 220,
  sidebarMaxWidth = 460,
}: PropsWithChildren<{
  ariaLabel: string;
  sidebarHeader?: ReactNode;
  sidebar?: ReactNode;
  sidebarFooter?: ReactNode;
  mainHeader?: ReactNode;
  className?: string;
  resizableSidebar?: boolean;
  sidebarResizeStorageKey?: string;
  sidebarDefaultWidth?: number;
  sidebarMinWidth?: number;
  sidebarMaxWidth?: number;
}>) {
  const rootRef = useRef<HTMLElement | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(sidebarDefaultWidth);
  const [isResizing, setIsResizing] = useState(false);
  const hasSidebar = sidebar !== null && sidebar !== undefined;
  const effectiveResizableSidebar = resizableSidebar && hasSidebar;

  const clampSidebarWidth = useCallback((value: number) => (
    Math.min(sidebarMaxWidth, Math.max(sidebarMinWidth, Math.round(value)))
  ), [sidebarMaxWidth, sidebarMinWidth]);

  useEffect(() => {
    if (!effectiveResizableSidebar || !sidebarResizeStorageKey) {
      return;
    }
    const storedValue = Number(window.localStorage.getItem(sidebarResizeStorageKey));
    if (Number.isFinite(storedValue) && storedValue > 0) {
      setSidebarWidth(clampSidebarWidth(storedValue));
    }
  }, [clampSidebarWidth, effectiveResizableSidebar, sidebarResizeStorageKey]);

  useEffect(() => {
    if (!effectiveResizableSidebar) {
      return;
    }
    rootRef.current?.style.setProperty('--workstation-split-sidebar-width', `${sidebarWidth}px`);
  }, [effectiveResizableSidebar, sidebarWidth]);

  const persistSidebarWidth = useCallback((nextWidth: number) => {
    if (!effectiveResizableSidebar || !sidebarResizeStorageKey) {
      return;
    }
    window.localStorage.setItem(sidebarResizeStorageKey, String(clampSidebarWidth(nextWidth)));
  }, [clampSidebarWidth, effectiveResizableSidebar, sidebarResizeStorageKey]);

  const updateSidebarWidth = useCallback((nextWidth: number, persist = false) => {
    const clampedWidth = clampSidebarWidth(nextWidth);
    setSidebarWidth(clampedWidth);
    if (persist) {
      persistSidebarWidth(clampedWidth);
    }
  }, [clampSidebarWidth, persistSidebarWidth]);

  const handleResizePointerDown = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if (!effectiveResizableSidebar || !rootRef.current) {
      return;
    }

    event.preventDefault();
    const rootRect = rootRef.current.getBoundingClientRect();
    let latestWidth = sidebarWidth;
    const previousCursor = document.body.style.cursor;
    const previousUserSelect = document.body.style.userSelect;

    const handlePointerMove = (moveEvent: globalThis.PointerEvent) => {
      latestWidth = clampSidebarWidth(moveEvent.clientX - rootRect.left);
      setSidebarWidth(latestWidth);
    };

    const handlePointerUp = () => {
      setIsResizing(false);
      persistSidebarWidth(latestWidth);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousUserSelect;
      window.removeEventListener('pointermove', handlePointerMove);
      window.removeEventListener('pointerup', handlePointerUp);
      window.removeEventListener('pointercancel', handlePointerUp);
    };

    setIsResizing(true);
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('pointermove', handlePointerMove);
    window.addEventListener('pointerup', handlePointerUp);
    window.addEventListener('pointercancel', handlePointerUp);
  }, [clampSidebarWidth, effectiveResizableSidebar, persistSidebarWidth, sidebarWidth]);

  const handleResizeKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (!effectiveResizableSidebar) {
      return;
    }

    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      updateSidebarWidth(sidebarWidth - 24, true);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      updateSidebarWidth(sidebarWidth + 24, true);
    } else if (event.key === 'Home') {
      event.preventDefault();
      updateSidebarWidth(sidebarMinWidth, true);
    } else if (event.key === 'End') {
      event.preventDefault();
      updateSidebarWidth(sidebarDefaultWidth, true);
    }
  }, [effectiveResizableSidebar, sidebarDefaultWidth, sidebarMinWidth, sidebarWidth, updateSidebarWidth]);

  return (
    <section
      ref={rootRef}
      className={joinClassNames(
        'workstation-split-workbench',
        effectiveResizableSidebar && 'workstation-split-workbench--resizable',
        isResizing && 'workstation-split-workbench--resizing',
        className,
      )}
      aria-label={ariaLabel}
    >
      {hasSidebar ? (
        <aside className="workstation-split-workbench__sidebar" aria-label={`${ariaLabel} list`}>
          {sidebarHeader ? (
            <div className="workstation-split-workbench__sidebar-header">
              {sidebarHeader}
            </div>
          ) : null}
          <div className="workstation-split-workbench__sidebar-scroll">
            {sidebar}
          </div>
          {sidebarFooter ? (
            <div className="workstation-split-workbench__sidebar-footer">
              {sidebarFooter}
            </div>
          ) : null}
        </aside>
      ) : null}
      {effectiveResizableSidebar ? (
        <div
          className="workstation-split-workbench__sidebar-resizer"
          role="separator"
          aria-label={`Resize ${ariaLabel} list`}
          aria-orientation="vertical"
          aria-valuemin={sidebarMinWidth}
          aria-valuemax={sidebarMaxWidth}
          aria-valuenow={sidebarWidth}
          tabIndex={0}
          onPointerDown={handleResizePointerDown}
          onKeyDown={handleResizeKeyDown}
        />
      ) : null}
      <section
        className={joinClassNames(
          'workstation-split-workbench__main',
          !mainHeader && 'workstation-split-workbench__main--no-header',
        )}
        aria-label={`${ariaLabel} detail`}
      >
        {mainHeader ? (
          <div className="workstation-split-workbench__main-header">
            {mainHeader}
          </div>
        ) : null}
        <div className="workstation-split-workbench__main-scroll">
          {children}
        </div>
      </section>
    </section>
  );
}
