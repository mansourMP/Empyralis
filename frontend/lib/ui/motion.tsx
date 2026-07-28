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

/* MAN-126 — this module now obeys docs/UI-CONTRACT.md §Motion, which it
   previously contradicted outright: "ease-out, capped at 200ms… No spring,
   no bounce, no overshoot."

   What changed and why:
   - `press` and `sheet` were springs (stiffness 420/320). A spring, by
     definition, overshoots and settles — that is bounce, and its real
     duration is unbounded. Both are now plain ease-out durations.
   - The three bespoke easing curves ([0.22,1,0.36,1], [0.16,1,0.3,1],
     [0.2,0.8,0.2,1] — all back-loaded "expo" curves that hang before
     arriving) collapse into ONE: the same cubic-bezier(0.2,0,0,1) that
     --ease-out carries in CSS. One curve, everywhere.
   - Durations mirror the CSS scale (--dur-1/2/3 = 90/140/200ms) rather than
     DESIGN_SYSTEM_MOTION's 120/160/220 — 220ms broke the contract's cap. */
const EASE_OUT = [0.2, 0, 0, 1] as const;
const EASE_IN = [0.4, 0, 1, 1] as const;

/** Seconds, because Framer/motion takes seconds. Mirrors --dur-1/2/3. */
const motionSeconds = {
  fast: 0.09,
  normal: 0.14,
  slow: 0.2,
} as const;

export const APP_MOTION_TIMINGS = {
  ms: { fast: 90, normal: 140, slow: 200 },
  seconds: motionSeconds,
  /** Kept for callers that still reference the older shared scale. */
  legacyMs: DESIGN_SYSTEM_MOTION,
} as const;

export const APP_MOTION_TRANSITIONS = {
  hover: { duration: motionSeconds.fast, ease: EASE_OUT },
  fade: { duration: motionSeconds.normal, ease: EASE_OUT },
  panel: { duration: motionSeconds.normal, ease: EASE_OUT },
  press: { duration: motionSeconds.fast, ease: EASE_OUT },
  sheet: { duration: motionSeconds.normal, ease: EASE_OUT },
  tab: { duration: motionSeconds.normal, ease: EASE_OUT },
  exit: { duration: motionSeconds.normal, ease: EASE_IN },
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
      /* No whileHover. A button that lifts and grows 1% under the cursor
         moves the target the user is aiming at, and a pointer already
         communicates hover perfectly well without the UI flinching. Press
         is the one interaction that earns motion, because the user is
         doing something and deserves the confirmation. */
      whileTap={interactive && !reduceMotion ? { scale: 0.98 } : undefined}
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
      /* Rows do not lift on hover — see MotionPressButton. A list of cards
         that each rise 1px as the cursor crosses them turns an ordinary
         scan down the page into a ripple of movement. */
      whileTap={interactive && !reduceMotion ? { scale: 0.98 } : undefined}
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
      /* Opacity only. The 10px rise-and-fall this used to do was the same
         tab-change entrance that was cut from the fleet surface: it delays
         the content you clicked for in order to tell you that you clicked,
         which the active-tab highlight already says. */
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
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
      /* Slides, no longer scales. The 0.985→1 scale read as a "pop" — the
         sheet arriving with a flourish rather than simply arriving. The
         short rise is kept because it says where the sheet came from. */
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={APP_MOTION_TRANSITIONS.sheet}
    >
      {children}
    </motion.div>
  );
}

export { AnimatePresence };
