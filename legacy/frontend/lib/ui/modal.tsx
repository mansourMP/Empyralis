'use client';

import { useEffect, useId, type HTMLAttributes, type PropsWithChildren, type ReactNode } from 'react';

import { AnimatePresence, MotionSheetSurface } from '@/lib/ui/motion';
import { AppButton, joinClassNames } from '@/lib/ui/primitives';
import {
  APP_LINE_HEIGHT,
  APP_RADIUS,
  APP_SHADOW,
  APP_SPACE_SCALE,
  APP_SPACING,
  APP_TYPE_SCALE,
} from '@/lib/ui/tokens';

export function Modal({
  open,
  title,
  description,
  actions,
  onClose,
  size = 'medium',
  className,
  children,
}: PropsWithChildren<{
  open: boolean;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  onClose: () => void;
  size?: 'small' | 'medium' | 'large';
  className?: string;
}>) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose, open]);

  const width =
    size === 'small'
      ? 'min(100%, 28rem)'
      : size === 'large'
        ? 'min(100%, 52rem)'
        : 'min(100%, 38rem)';
  const titleId = useId();
  const descriptionId = useId();

  return (
    <AnimatePresence>
      {open ? (
        <div
          aria-modal="true"
          aria-labelledby={titleId}
          aria-describedby={description ? descriptionId : undefined}
          role="dialog"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 'calc(var(--z-modal, 100) + 20)',
            display: 'grid',
            placeItems: 'center',
            padding: APP_SPACING[5],
            background: 'color-mix(in srgb, var(--app-bg-overlay) 76%, transparent 24%)',
            backdropFilter: 'blur(12px)',
          }}
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              onClose();
            }
          }}
        >
          <MotionSheetSurface
            className={joinClassNames('app-modal', className)}
            style={{
              width,
              display: 'grid',
              gridTemplateRows: 'auto minmax(0, 1fr) auto',
              gap: APP_SPACE_SCALE.px16,
              maxHeight: `calc(100vh - ${APP_SPACING[10]})`,
              padding: `${APP_SPACING[4]} ${APP_SPACING[4]} ${APP_SPACING[4]}`,
              borderRadius: APP_SPACE_SCALE.px18,
              border: '1px solid var(--app-border-subtle)',
              background: 'color-mix(in srgb, var(--app-bg-panel) 90%, var(--app-bg-overlay) 10%)',
              boxShadow: APP_SHADOW.panel,
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'start',
                gap: APP_SPACING[3],
              }}
            >
              <div style={{ display: 'grid', gap: APP_SPACING[1], minWidth: 0 }}>
                <strong id={titleId} style={{ color: 'var(--app-text-primary)', fontSize: APP_TYPE_SCALE[16] }}>{title}</strong>
                {description ? (
                  <span
                    id={descriptionId}
                    style={{
                      color: 'var(--app-text-secondary)',
                      fontSize: APP_TYPE_SCALE[13],
                      lineHeight: APP_LINE_HEIGHT.relaxed,
                    }}
                  >
                    {description}
                  </span>
                ) : null}
              </div>
              <AppButton type="button" tone="ghost" onClick={onClose}>
                Close
              </AppButton>
            </div>

            <div
              style={{
                display: 'grid',
                gap: APP_SPACE_SCALE.px14,
                minHeight: 0,
                overflowY: 'auto',
                paddingRight: APP_SPACING[1],
              }}
            >
              {children}
            </div>

            {actions ? (
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: APP_SPACE_SCALE.px10, flexWrap: 'wrap' }}>
                {actions}
              </div>
            ) : null}
          </MotionSheetSurface>
        </div>
      ) : null}
    </AnimatePresence>
  );
}

export function ModalSection({
  title,
  description,
  className,
  children,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement> & {
  title: ReactNode;
  description?: ReactNode;
}>) {
  return (
    <div
      {...props}
      className={joinClassNames('app-modal-section', className)}
      style={{
        display: 'grid',
        gap: APP_SPACE_SCALE.px10,
        padding: `${APP_SPACE_SCALE.px14} ${APP_SPACE_SCALE.px14}`,
        borderRadius: APP_RADIUS.lg,
        border: '1px solid var(--app-border-subtle)',
        background: 'color-mix(in srgb, var(--app-bg-panel-elevated) 82%, var(--app-bg-overlay) 18%)',
      }}
    >
      <div style={{ display: 'grid', gap: APP_SPACING[1] }}>
        <strong style={{ color: 'var(--app-text-primary)', fontSize: APP_TYPE_SCALE[14] }}>{title}</strong>
        {description ? (
          <span
            style={{
              color: 'var(--app-text-secondary)',
              fontSize: APP_TYPE_SCALE[12],
              lineHeight: APP_LINE_HEIGHT.body,
            }}
          >
            {description}
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}
