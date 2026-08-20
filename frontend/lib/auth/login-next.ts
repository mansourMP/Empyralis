/**
 * Where a signed-out visitor goes, and where they come back to.
 *
 * MAN-358. A deep link from a channel message ("I created GEN-12 for you:
 * https://…/w/{ws}/projects/{p}/tasks/{t}") is opened by whoever is reading
 * Telegram, on whatever device is in their hand — which is very often a
 * browser with no Empyralis session. Until this module existed, that visitor
 * hit `(account)/layout.tsx`'s bare `redirect('/login')`, signed in, and
 * landed on the workspace root. The link they tapped had a specific
 * destination and the product silently discarded it; the person then had to
 * find the task by hand, which for the one identifier they were given
 * (GEN-12) is a search the UI does not offer.
 *
 * Nothing here is new machinery. `/login`, `/signup` and `/verify-email` all
 * already read `?next=` and all three already carried a byte-identical
 * private `safeNextPath`, each with a comment saying it mirrored the others.
 * This is that one function, in one place, plus the builder for the redirect
 * that produces the parameter — so the rule that decides what a `next` may be
 * and the code that writes one can never disagree.
 *
 * THE RULE: a `next` is only ever a same-origin, path-relative destination.
 * Never an absolute URL, never protocol-relative (`//evil.example` is a valid
 * URL to a browser and would make the login page an open redirect), never
 * backslash-bearing (some browsers normalise `\` to `/`, so `/\evil.example`
 * is protocol-relative in disguise). Anything failing that is not rejected
 * with an error — it degrades to `/`, which is exactly where the product used
 * to send everybody anyway.
 */

/** Set by `proxy.ts` on every request it handles, so a SERVER component can
 *  know the path being rendered — Next.js exposes no other way to read it
 *  from a layout. Always `set` (never appended), so a client-supplied header
 *  of the same name is overwritten rather than trusted; `safeNextPath` is the
 *  backstop for the few asset paths the proxy matcher skips. */
export const REQUEST_PATHNAME_HEADER = 'x-empyralis-pathname';

export function safeNextPath(rawNext: string): string {
  const trimmed = String(rawNext || '').trim();
  if (!trimmed || !trimmed.startsWith('/') || trimmed.startsWith('//') || trimmed.includes('\\')) {
    return '/';
  }
  return trimmed;
}

/**
 * The `/login` URL that will bring this visitor back to where they were.
 *
 * A destination of `/` gets NO `next` at all rather than `?next=%2F`: the
 * parameter would say nothing and would show up in the address bar of every
 * ordinary "signed out, went to the front door" visit.
 */
export function loginHrefForPath(rawPath: string): string {
  const target = safeNextPath(rawPath);
  if (target === '/') {
    return '/login';
  }
  return `/login?next=${encodeURIComponent(target)}`;
}
