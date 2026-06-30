'use client';

import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  CSSProperties,
  HTMLAttributes,
  InputHTMLAttributes,
  PropsWithChildren,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react';
import { useEffect } from 'react';
import { ChevronRight } from 'lucide-react';

import { MotionPressButton, MotionSurfaceRow } from '@/lib/ui/motion';

function joinClassNames(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(' ');
}

export function AppSurfaceRoot({
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div {...props} className={joinClassNames('app-surface-root', className)}>
      {children}
    </div>
  );
}

export function AppSurfaceCard({
  title,
  description,
  actions,
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLElement> & {
  title: string;
  description?: string;
  actions?: ReactNode;
}>) {
  return (
    <section {...props} className={joinClassNames('app-surface-card', className)}>
      <div className="app-surface-card__header">
        <div className="app-surface-card__copy">
          <h2 className="app-surface-title">{title}</h2>
          {description ? <p className="app-surface-description">{description}</p> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function AppNotice({
  tone = 'neutral',
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement> & {
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
}>) {
  return (
    <div
      {...props}
      className={joinClassNames(
        'app-surface-notice',
        tone !== 'neutral' && `app-surface-notice--${tone}`,
        className,
      )}
    >
      {children}
    </div>
  );
}

export function AppShinyText({
  children,
  className,
  shimmerWidth = 96,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLSpanElement> & {
  shimmerWidth?: number;
}>) {
  return (
    <span
      {...props}
      style={{
        '--app-shiny-width': `${shimmerWidth}px`,
        ...props.style,
      } as CSSProperties}
      className={joinClassNames('app-shiny-text', className)}
    >
      {children}
    </span>
  );
}

export function AppGetStartedButton(
  props:
    | (PropsWithChildren<AnchorHTMLAttributes<HTMLAnchorElement>> & {
        href: string;
      })
    | (PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement>> & {
        href?: undefined;
      }),
) {
  const { children = 'Get Started', className, ...rest } = props as PropsWithChildren<
    AnchorHTMLAttributes<HTMLAnchorElement> & ButtonHTMLAttributes<HTMLButtonElement>
  >;

  const content = (
    <>
      <span className="app-get-started-button__label">{children}</span>
      <i className="app-get-started-button__rail">
        <ChevronRight size={16} strokeWidth={2} aria-hidden="true" />
      </i>
    </>
  );

  if ('href' in rest && typeof rest.href === 'string') {
    const { href, ...anchorProps } = rest as AnchorHTMLAttributes<HTMLAnchorElement>;
    return (
      <a href={href} {...anchorProps} className={joinClassNames('app-get-started-button', className)}>
        {content}
      </a>
    );
  }

  return (
    <button
      {...(rest as ButtonHTMLAttributes<HTMLButtonElement>)}
      className={joinClassNames('app-get-started-button', className)}
    >
      {content}
    </button>
  );
}

export function AppSurfaceList({
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div {...props} className={joinClassNames('app-surface-list', className)}>
      {children}
    </div>
  );
}

export function AppSurfaceListItem({
  title,
  subtitle,
  description,
  actions,
  className,
  ...props
}: HTMLAttributes<HTMLElement> & {
  title: string;
  subtitle?: string | null;
  description?: string | null;
  actions?: ReactNode;
}) {
  return (
    <MotionSurfaceRow {...props} className={joinClassNames('app-surface-list-item', className)}>
      <div className="app-surface-list-item__row">
        <div className="app-surface-list-item__copy">
          <div className="app-surface-list-item__title">{title}</div>
          {subtitle ? <div className="app-surface-list-item__subtitle">{subtitle}</div> : null}
        </div>
        {actions}
      </div>
      {description ? <p className="app-surface-list-item__description">{description}</p> : null}
    </MotionSurfaceRow>
  );
}

export function AppSurfaceStatGrid({
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLDivElement>>) {
  return (
    <div {...props} className={joinClassNames('app-surface-stat-grid', className)}>
      {children}
    </div>
  );
}

export function AppSurfaceStat({
  label,
  value,
  hint,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  label: string;
  value: ReactNode;
  hint: string;
}) {
  return (
    <div {...props} className={joinClassNames('app-surface-stat', className)}>
      <span className="app-surface-stat__label">{label}</span>
      <strong className="app-surface-stat__value">{value}</strong>
      <span className="app-surface-stat__hint">{hint}</span>
    </div>
  );
}

export function AppButton({
  tone = 'primary',
  className,
  children,
  ...props
}: PropsWithChildren<ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: 'primary' | 'secondary' | 'danger' | 'ghost';
}>) {
  return (
    <MotionPressButton
      {...props}
      className={joinClassNames('app-button', `app-button--${tone}`, className)}
    >
      {children}
    </MotionPressButton>
  );
}

export function AppInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={joinClassNames('app-field', props.className)} />;
}

export function AppSelect(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={joinClassNames('app-select', props.className)} />;
}

export function AppTextarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={joinClassNames('app-textarea', props.className)} />;
}

export function AppEmptyState({
  title,
  body,
  actions,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  title: string;
  body: string;
  actions?: ReactNode;
}) {
  return (
    <div {...props} className={joinClassNames('app-empty-state', className)}>
      <div className="app-empty-state__title">{title}</div>
      <div className="app-empty-state__body">{body}</div>
      {actions}
    </div>
  );
}

type AppOverlayBaseProps = PropsWithChildren<{
  open: boolean;
  title?: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
  onOpenChange: (open: boolean) => void;
}>;

function useDismissableOverlay(open: boolean, onOpenChange: (open: boolean) => void) {
  useEffect(() => {
    if (!open) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onOpenChange(false);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onOpenChange, open]);
}

function AppOverlayHeader({
  title,
  description,
  actions,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
}) {
  if (!title && !description && !actions) {
    return null;
  }

  return (
    <div className="app-overlay__header">
      <div className="app-overlay__copy">
        {title ? <h2 className="app-overlay__title">{title}</h2> : null}
        {description ? <p className="app-overlay__description">{description}</p> : null}
      </div>
      {actions ? <div className="app-overlay__actions">{actions}</div> : null}
    </div>
  );
}

export function AppModal({
  open,
  title,
  description,
  actions,
  children,
  className,
  onOpenChange,
}: AppOverlayBaseProps) {
  useDismissableOverlay(open, onOpenChange);

  if (!open) {
    return null;
  }

  return (
    <div className="app-overlay app-overlay--modal" role="presentation" onMouseDown={() => onOpenChange(false)}>
      <section
        aria-modal="true"
        className={joinClassNames('app-overlay__surface app-overlay__surface--modal', className)}
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <AppOverlayHeader title={title} description={description} actions={actions} />
        <div className="app-overlay__body">{children}</div>
      </section>
    </div>
  );
}

export function AppDrawer({
  open,
  title,
  description,
  actions,
  children,
  className,
  onOpenChange,
}: AppOverlayBaseProps) {
  useDismissableOverlay(open, onOpenChange);

  if (!open) {
    return null;
  }

  return (
    <div className="app-overlay app-overlay--drawer" role="presentation" onMouseDown={() => onOpenChange(false)}>
      <aside
        aria-modal="true"
        className={joinClassNames('app-overlay__surface app-overlay__surface--drawer', className)}
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <AppOverlayHeader title={title} description={description} actions={actions} />
        <div className="app-overlay__body">{children}</div>
      </aside>
    </div>
  );
}

export { joinClassNames };
