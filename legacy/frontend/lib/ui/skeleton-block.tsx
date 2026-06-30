'use client';

import type { CSSProperties, HTMLAttributes } from 'react';

import { joinClassNames } from '@/lib/ui/primitives';
import { APP_RADIUS, APP_SPACING } from '@/lib/ui/tokens';

export function SkeletonBlock({
  width = '100%',
  height = APP_SPACING[4],
  radius = APP_RADIUS.md,
  className,
  style,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  width?: CSSProperties['width'];
  height?: CSSProperties['height'];
  radius?: CSSProperties['borderRadius'];
}) {
  return (
    <div
      {...props}
      className={joinClassNames('app-skeleton-block', className)}
      style={{
        width,
        height,
        borderRadius: radius,
        ...style,
      }}
    />
  );
}
