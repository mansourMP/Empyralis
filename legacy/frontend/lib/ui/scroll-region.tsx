import { forwardRef, type CSSProperties, type HTMLAttributes, type PropsWithChildren } from 'react';

function joinClassNames(...values: Array<string | false | null | undefined>) {
  return values.filter(Boolean).join(' ');
}

export const ScrollRegion = forwardRef<HTMLDivElement, PropsWithChildren<HTMLAttributes<HTMLDivElement>>>(function ScrollRegion({
  children,
  className,
  style,
  ...props
}, ref) {
  return (
    <div
      {...props}
      ref={ref}
      className={joinClassNames('app-scroll-region', className)}
      style={{
        minWidth: 0,
        minHeight: 0,
        height: '100%',
        overflow: 'auto',
        overscrollBehavior: 'contain',
        ...((style ?? {}) as CSSProperties),
      }}
    >
      {children}
    </div>
  );
});
