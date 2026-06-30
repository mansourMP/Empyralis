'use client';

import type { HTMLAttributes, PropsWithChildren, ReactNode } from 'react';

import { MotionInlineBanner } from '@/lib/ui/motion';
import { joinClassNames } from '@/lib/ui/primitives';
import {
  APP_LINE_HEIGHT,
  APP_RADIUS,
  APP_SHADOW,
  APP_SPACE_SCALE,
  APP_SPACING,
  APP_TYPE_SCALE,
} from '@/lib/ui/tokens';

type StateBannerTone = 'neutral' | 'success' | 'warning' | 'danger';

function toneStyles(tone: StateBannerTone): {
  border: string;
  background: string;
  accent: string;
} {
  if (tone === 'success') {
    return {
      border: 'color-mix(in srgb, var(--app-success) 45%, var(--app-border-subtle) 55%)',
      background: 'color-mix(in srgb, var(--app-success) 10%, var(--app-bg-panel) 90%)',
      accent: 'var(--app-success)',
    };
  }
  if (tone === 'warning') {
    return {
      border: 'color-mix(in srgb, var(--app-warning) 45%, var(--app-border-subtle) 55%)',
      background: 'color-mix(in srgb, var(--app-warning) 10%, var(--app-bg-panel) 90%)',
      accent: 'var(--app-warning)',
    };
  }
  if (tone === 'danger') {
    return {
      border: 'color-mix(in srgb, var(--app-danger) 45%, var(--app-border-subtle) 55%)',
      background: 'color-mix(in srgb, var(--app-danger) 10%, var(--app-bg-panel) 90%)',
      accent: 'var(--app-danger)',
    };
  }
  return {
    border: 'var(--app-border-subtle)',
    background: 'color-mix(in srgb, var(--app-bg-panel-elevated) 84%, var(--app-bg-overlay) 16%)',
    accent: 'var(--app-accent)',
  };
}

export function StateBanner({
  tone = 'neutral',
  title,
  detail,
  actions,
  className,
  children,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement> & {
  tone?: StateBannerTone;
  title?: ReactNode;
  detail?: ReactNode;
  actions?: ReactNode;
}>) {
  const palette = toneStyles(tone);
  return (
    <MotionInlineBanner
      {...props}
      className={joinClassNames('app-state-banner', className)}
      style={{
        display: 'grid',
        gap: APP_SPACING[2],
        padding: `${APP_SPACING[3]} ${APP_SPACE_SCALE.px14}`,
        borderRadius: APP_RADIUS.lg,
        border: `1px solid ${palette.border}`,
        background: palette.background,
        boxShadow: APP_SHADOW.panel,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'start',
          justifyContent: 'space-between',
          gap: APP_SPACE_SCALE.px14,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'grid', gap: APP_SPACING[1] }}>
          {title ? (
            <strong style={{ color: 'var(--app-text-primary)', fontSize: APP_TYPE_SCALE[14] }}>
              {title}
            </strong>
          ) : null}
          {detail ? (
            <span
              style={{
                color: 'var(--app-text-secondary)',
                fontSize: APP_TYPE_SCALE[13],
                lineHeight: APP_LINE_HEIGHT.body,
              }}
            >
              {detail}
            </span>
          ) : null}
        </div>
        {actions}
      </div>
      {children ? (
        <div style={{ color: palette.accent, fontSize: APP_TYPE_SCALE[12], lineHeight: APP_LINE_HEIGHT.compact }}>
          {children}
        </div>
      ) : null}
    </MotionInlineBanner>
  );
}
