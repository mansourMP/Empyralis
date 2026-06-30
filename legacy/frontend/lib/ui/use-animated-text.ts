"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Progressively reveals *targetText* one token at a time.
 *
 * When the separator is a non-empty string the text is split by that
 * delimiter (e.g. `" "` for word-by-word).  When it is an empty string
 * the text is revealed character-by-character.
 *
 * The hook is driven by a timer so the reveal speed is independent of
 * how fast chunks arrive from the network — it always *animates* at
 * ~tokens-per-second regardless of burstiness.
 */
export function useAnimatedText(
  targetText: string,
  separator: string = " ",
  speed: number = 30,
): string {
  // Tokens we are aiming toward
  const tokens = separator
    ? targetText.split(separator)
    : targetText.split("");

  const targetCount = tokens.length;

  // How many tokens are currently visible
  const [visible, setVisible] = useState(0);
  const visibleRef = useRef(visible);
  visibleRef.current = visible;

  useEffect(() => {
    // Reset when the target changes completely (new turn)
    if (targetCount === 0) {
      setVisible(0);
      return;
    }
    // Already fully revealed — nothing to do
    if (visibleRef.current >= targetCount) return;

    // Use requestAnimationFrame so we don't fight React's own rAF
    // scheduling; this keeps the animation smooth and consent-based.
    let raf: number | null = null;
    let last = performance.now();

    const tick = (now: number) => {
      if (visibleRef.current >= targetCount) {
        raf = null;
        return;
      }
      const elapsed = now - last;
      const steps = Math.floor(elapsed / speed);
      if (steps > 0) {
        last = now - (elapsed - steps * speed);
        setVisible((c) => Math.min(c + steps, targetCount));
      }
      raf = requestAnimationFrame(tick);
    };

    raf = requestAnimationFrame(tick);
    return () => {
      if (raf !== null) cancelAnimationFrame(raf);
    };
  }, [targetText, targetCount, speed, separator]);

  if (!separator) {
    // character-by-character
    return targetText.slice(0, visible);
  }
  return tokens.slice(0, visible).join(separator);
}
