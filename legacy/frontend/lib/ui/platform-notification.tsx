'use client';

import type { ButtonHTMLAttributes, HTMLAttributes, PropsWithChildren, ReactNode } from 'react';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Bell, CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react';

import { joinClassNames } from '@/lib/ui/primitives';

export type PlatformNotificationTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info';

type PlatformNotificationAction = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
};

function NotificationIcon({ tone }: { tone: PlatformNotificationTone }) {
  if (tone === 'success') {
    return <CircleCheck aria-hidden="true" />;
  }
  if (tone === 'warning') {
    return <TriangleAlert aria-hidden="true" />;
  }
  if (tone === 'danger') {
    return <CircleAlert aria-hidden="true" />;
  }
  if (tone === 'info') {
    return <Bell aria-hidden="true" />;
  }
  return <Info aria-hidden="true" />;
}

function NotificationAction({ action }: { action: PlatformNotificationAction }) {
  const { label, className, type, ...actionProps } = action;
  return (
    <button
      {...actionProps}
      type={type ?? 'button'}
      className={joinClassNames('app-platform-notification__action', className)}
    >
      {label}
    </button>
  );
}

export function PlatformNotification({
  tone = 'neutral',
  title,
  detail,
  action,
  onClose,
  className,
  children,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement> & {
  tone?: PlatformNotificationTone;
  title: ReactNode;
  detail?: ReactNode;
  action?: PlatformNotificationAction;
  onClose?: () => void;
}>) {
  const [mounted, setMounted] = useState(false);
  const urgent = tone === 'danger' || tone === 'warning';

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return null;
  }

  const notification = (
    <div className="app-notification-viewport">
      <div
        {...props}
        data-tone={tone}
        className={joinClassNames('app-platform-notification', className)}
      >
        <div className="app-platform-notification__icon">
          <NotificationIcon tone={tone} />
        </div>
        <div className="app-platform-notification__content">
          <div
            className="app-platform-notification__message"
            role={urgent ? 'alert' : 'status'}
            aria-atomic="true"
          >
            <div className="app-platform-notification__title">{title}</div>
            {detail ? <div className="app-platform-notification__detail">{detail}</div> : null}
            {children ? <div className="app-platform-notification__body">{children}</div> : null}
          </div>
          {action ? <NotificationAction action={action} /> : null}
        </div>
        {onClose ? (
          <button
            type="button"
            aria-label="Dismiss notification"
            className="app-platform-notification__close"
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </div>
  );

  return createPortal(notification, document.body);
}
