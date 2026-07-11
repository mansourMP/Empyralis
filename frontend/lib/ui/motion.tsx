'use client';

import type { ButtonHTMLAttributes, HTMLAttributes, PropsWithChildren } from 'react';

import { AnimatePresence, motion, useReducedMotion } from 'motion/react';

import { DESIGN_SYSTEM_MOTION } from '../../../shared/design-system/tokens';

const joinClassNames = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(' ');

type SafeButtonProps = Omit<
  ButtonHTMLAttributes<HTMLButtonElement>,
  | 'draggable'
  | 'onDrag'
  | 'onDragStart'
  | 'onDragEnd'
  | 'onDragCapture'
  | 'onAnimationStart'
  | 'onAnimationStartCapture'
  | 'onAnimationEnd'
  | 'onAnimationEndCapture'
  | 'onAnimationIteration'
  | 'onAnimationIterationCapture'
>;

type SafeElementProps = Omit<
  HTMLAttributes<HTMLElement>,
  | 'draggable'
  | 'onDrag'
  | 'onDragStart'
  | 'onDragEnd'
  | 'onDragCapture'
  | 'onAnimationStart'
  | 'onAnimationStartCapture'
  | 'onAnimationEnd'
  | 'onAnimationEndCapture'
  | 'onAnimationIteration'
  | 'onAnimationIterationCapture'
>;

type SafeDivProps = Omit<
  HTMLAttributes<HTMLDivElement>,
  | 'draggable'
  | 'onDrag'
  | 'onDragStart'
  | 'onDragEnd'
  | 'onDragCapture'
  | 'onAnimationStart'
  | 'onAnimationStartCapture'
  | 'onAnimationEnd'
  | 'onAnimationEndCapture'
  | 'onAnimationIteration'
  | 'onAnimationIterationCapture'
>;

const motionSeconds = {
  fast: DESIGN_SYSTEM_MOTION.fast / 1000,
  normal: DESIGN_SYSTEM_MOTION.normal / 1000,
  slow: DESIGN_SYSTEM_MOTION.slow / 1000,
} as const;

export const APP_MOTION_TIMINGS = {
  ms: DESIGN_SYSTEM_MOTION,
  seconds: motionSeconds,
} as const;

export const APP_MOTION_TRANSITIONS = {
  hover: {
    duration: motionSeconds.fast,
    ease: [0.22, 1, 0.36, 1] as const,
  },
  fade: {
    duration: motionSeconds.normal,
    ease: [0.16, 1, 0.3, 1] as const,
  },
  panel: {
    duration: motionSeconds.normal,
    ease: [0.16, 1, 0.3, 1] as const,
  },
  press: {
    type: 'spring' as const,
    stiffness: 420,
    damping: 28,
    mass: 0.82,
  },
  sheet: {
    type: 'spring' as const,
    stiffness: 320,
    damping: 30,
    mass: 0.92,
  },
  tab: {
    duration: motionSeconds.fast,
    ease: [0.2, 0.8, 0.2, 1] as const,
  },
} as const;

export function MotionPressButton({
  className,
  children,
  disabled,
  ...props
}: PropsWithChildren<SafeButtonProps>) {
  const reduceMotion = useReducedMotion();
  const interactive = !disabled;
  return (
    <motion.button
      {...props}
      disabled={disabled}
      className={className}
      whileHover={
        interactive && !reduceMotion
          ? { y: -1, scale: 1.01 }
          : undefined
      }
      whileTap={
        interactive && !reduceMotion
          ? { scale: 0.985, y: 0 }
          : undefined
      }
      transition={APP_MOTION_TRANSITIONS.press}
    >
      {children}
    </motion.button>
  );
}

export function MotionSurfaceRow({
  className,
  children,
  interactive = true,
  ...props
}: PropsWithChildren<SafeElementProps & {
  interactive?: boolean;
}>) {
  const reduceMotion = useReducedMotion();
  return (
    <motion.article
      {...props}
      className={className}
      whileHover={
        interactive && !reduceMotion
          ? { y: -1 }
          : undefined
      }
      whileTap={
        interactive && !reduceMotion
          ? { scale: 0.995 }
          : undefined
      }
      transition={APP_MOTION_TRANSITIONS.press}
    >
      {children}
    </motion.article>
  );
}

export function MotionInlineBanner({
  className,
  children,
  ...props
}: PropsWithChildren<SafeDivProps>) {
  return (
    <motion.div
      {...props}
      className={className}
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -6 }}
      transition={APP_MOTION_TRANSITIONS.fade}
    >
      {children}
    </motion.div>
  );
}

export function MotionSlidePanel({
  className,
  children,
  direction = 'right',
  ...props
}: PropsWithChildren<SafeDivProps & {
  direction?: 'left' | 'right';
}>) {
  const offset = direction === 'left' ? -20 : 20;
  return (
    <motion.div
      {...props}
      className={joinClassNames('app-motion-panel', className)}
      initial={{ opacity: 0, x: offset }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: offset * 0.7 }}
      transition={APP_MOTION_TRANSITIONS.panel}
    >
      {children}
    </motion.div>
  );
}

export function MotionTabPanel({
  className,
  children,
  ...props
}: PropsWithChildren<SafeDivProps>) {
  return (
    <motion.div
      {...props}
      className={joinClassNames('app-motion-tab-panel', className)}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      transition={APP_MOTION_TRANSITIONS.tab}
    >
      {children}
    </motion.div>
  );
}

export function MotionSheetSurface({
  className,
  children,
  ...props
}: PropsWithChildren<SafeDivProps>) {
  return (
    <motion.div
      {...props}
      className={joinClassNames('app-motion-sheet', className)}
      initial={{ opacity: 0, y: 16, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 12, scale: 0.99 }}
      transition={APP_MOTION_TRANSITIONS.sheet}
    >
      {children}
    </motion.div>
  );
}

export { AnimatePresence };
