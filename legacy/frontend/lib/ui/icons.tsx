'use client';

import type { ReactNode, SVGProps } from 'react';

import { joinClassNames } from '@/lib/ui/primitives';

type AppIconProps = SVGProps<SVGSVGElement> & {
  size?: number;
};

function AppIcon({
  size = 16,
  className,
  children,
  ...props
}: AppIconProps & {
  children: ReactNode;
}) {
  return (
    <svg
      {...props}
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.45"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={joinClassNames('app-icon', className)}
    >
      {children}
    </svg>
  );
}

export function SparkIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <path d="M8 1.5 9.5 6 14 7.5 9.5 9 8 13.5 6.5 9 2 7.5 6.5 6 8 1.5Z" />
    </AppIcon>
  );
}

export function ActivityIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <path d="M1.75 8h2.4l1.55-3.45L8.3 11l2.05-4h3.9" />
    </AppIcon>
  );
}

export function InboxIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <path d="M2 4.25h12v7.5H10.5L8 14l-2.5-2.25H2v-7.5Z" />
      <path d="M5.25 7.75h5.5" />
    </AppIcon>
  );
}

export function SettingsIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <circle cx="8" cy="8" r="2.15" />
      <path d="M8 1.75v1.5M8 12.75v1.5M12.25 3.75l-1.05 1.05M4.8 11.2 3.75 12.25M14.25 8h-1.5M3.25 8h-1.5M12.25 12.25 11.2 11.2M4.8 4.8 3.75 3.75" />
    </AppIcon>
  );
}

export function StudioIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <rect x="2.25" y="2.25" width="4.5" height="4.5" rx="1" />
      <rect x="9.25" y="2.25" width="4.5" height="4.5" rx="1" />
      <rect x="2.25" y="9.25" width="4.5" height="4.5" rx="1" />
      <rect x="9.25" y="9.25" width="4.5" height="4.5" rx="1" />
    </AppIcon>
  );
}

export function WorkspaceIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <path d="M2 4.5 8 2l6 2.5v7L8 14 2 11.5V4.5Z" />
      <path d="M2 4.5 8 7l6-2.5" />
      <path d="M8 7v7" />
    </AppIcon>
  );
}

export function ProfileIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <circle cx="8" cy="5.25" r="2.25" />
      <path d="M3.25 13c.7-2.1 2.3-3.15 4.75-3.15S12.05 10.9 12.75 13" />
    </AppIcon>
  );
}

export function PanelsIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <rect x="2" y="3" width="12" height="10" rx="1.5" />
      <path d="M6.5 3v10" />
    </AppIcon>
  );
}

export function ComposeIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <path d="M10.5 2.5 13.5 5.5" />
      <path d="M3 13l2.7-.45L13.5 4.75a1.5 1.5 0 0 0-2.1-2.1L3.6 10.45 3 13Z" />
      <path d="M9.5 4.5 11.5 6.5" />
    </AppIcon>
  );
}

export function PaperclipIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <path d="M6 8.75 9.9 4.85a2 2 0 1 1 2.85 2.8L7.45 13a3.5 3.5 0 1 1-4.95-4.95l5.15-5.15" />
    </AppIcon>
  );
}

export function MemoryIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <rect x="2.5" y="3" width="11" height="10" rx="2" />
      <path d="M5.25 6.25h5.5" />
      <path d="M5.25 8.5h5.5" />
      <path d="M5.25 10.75h3.25" />
    </AppIcon>
  );
}

export function SendIcon(props: AppIconProps) {
  return (
    <AppIcon {...props}>
      <path d="M2 13.25 14 8 2 2.75l1.7 4.1L10 8 3.7 9.15 2 13.25Z" />
    </AppIcon>
  );
}
