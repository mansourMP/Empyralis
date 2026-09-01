import { useCallback, useEffect, useRef, useState } from "react";

/**
 * The one place a "Copy link" control talks to navigator.clipboard.
 *
 * `navigator.clipboard` is undefined over plain http:// in some browsers,
 * and without a permission grant in others — `navigator.clipboard?.writeText(
 * ...)` in that case evaluates to `undefined` rather than a rejected
 * promise, so an `await` on it resolves cleanly with nothing having
 * happened. A caller that doesn't check for that silently claims success on
 * a click that copied nothing (found live in DocumentDetailView.tsx's first
 * "Copy link" pass — its own comment claimed the click handler followed
 * MembersSection.tsx's confirmed-before-shown convention; it did not, it set
 * `copied=true` unconditionally on click).
 *
 * CLAUDE.md: "never claim success if navigator.clipboard rejected —
 * reporting failure on a success is the worst case, not the safest." Every
 * "Copy link" control in this codebase should call this and gate its
 * "Copied!" state on the returned boolean, never on merely having clicked.
 */
export async function copyTextToClipboard(text: string): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.clipboard) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export type CopyLinkState = "idle" | "copied" | "failed";

/**
 * Shared behavior behind every "Copy link" control: a three-state label
 * swap (idle / copied / failed) that reverts on its own, gated on
 * `copyTextToClipboard`'s real return value rather than the click itself —
 * see this module's own header. One hook, reused by every surface that
 * offers Copy link, rather than three independent re-implementations of the
 * same success/failure branching drifting apart over time.
 */
export function useCopyLinkState(getUrl: () => string, resetAfterMs = 1500) {
  const [state, setState] = useState<CopyLinkState>("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const copy = useCallback(async () => {
    const ok = await copyTextToClipboard(getUrl());
    setState(ok ? "copied" : "failed");
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setState("idle"), resetAfterMs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetAfterMs]);

  const reset = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setState("idle");
  }, []);

  return { state, copy, reset };
}
