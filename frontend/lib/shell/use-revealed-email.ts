'use client';

import { useEffect, useState } from 'react';

import { revealEmail } from '@/lib/shell/ssr-safe-email';

/**
 * 2026-08-14 — see `ssr-safe-email.ts` for the full mechanism this exists
 * to defend against (Cloudflare's email-obfuscation rewrite breaking
 * hydration). Every account/owner email this app has already been carrying
 * server-side arrives here XOR-obfuscated, not plaintext — this hook is
 * the only sanctioned way to recover the real address.
 *
 * The decode happens ONLY inside `useEffect`, which React guarantees never
 * runs during server rendering (or during the client's first hydrating
 * render — only after commit). That is not an implementation detail, it is
 * the entire point: if the decode ran synchronously in render, the real
 * address would land right back in the server-rendered HTML or in the
 * client's very first render output, undoing the fix. `null` is returned
 * until that effect has run — callers must render a safe placeholder
 * (a role, "Owner", anything non-email-shaped) for that state rather than
 * blocking on it, exactly like every other hydration-safe "mounted" flag
 * already used in this codebase (see FleetAgentDetail's hydration-effect
 * pattern, referenced in CLAUDE.md).
 */
export function useRevealedEmail(obfuscatedEmail: string | null | undefined): string | null {
  const [revealed, setRevealed] = useState<string | null>(null);

  useEffect(() => {
    if (!obfuscatedEmail) {
      setRevealed(null);
      return;
    }
    setRevealed(revealEmail(obfuscatedEmail) || null);
  }, [obfuscatedEmail]);

  return revealed;
}
