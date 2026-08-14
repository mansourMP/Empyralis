/**
 * The real Content-Security-Policy for the Next.js app — the largest
 * remaining hardening item after the 2026-08-12 security-headers pass
 * (see deploy/nginx-empyralis.conf and CLAUDE.md). That pass shipped only
 * `frame-ancestors 'none'` at the nginx layer deliberately: a policy
 * carrying `'unsafe-inline' 'unsafe-eval'` looks like a CSP in a header
 * dump and stops nothing, and a real `script-src` needs a per-request
 * nonce nginx cannot generate (it doesn't know what Next.js rendered).
 *
 * This module owns the POLICY STRING. `frontend/proxy.ts` owns generating
 * the nonce and attaching the header to every response — nginx no longer
 * emits a Content-Security-Policy header at all (see the comment in
 * deploy/nginx-empyralis.conf), so there is exactly one place a browser's
 * CSP can come from.
 *
 * Every directive here is backed by a real finding, not a guess:
 *
 *   script-src   'self' 'nonce-<per-request>' 'strict-dynamic'
 *     — the standard "strict CSP" shape (Next.js's own docs, Google's CSP
 *     guide). 'strict-dynamic' makes browsers that support it ignore the
 *     'self' fallback and trust only nonce-carrying scripts plus whatever
 *     THEY load; browsers that don't support it fall back to 'self' +
 *     nonce. No CSS-in-JS runtime in this app (grepped: no
 *     styled-components/emotion/styled-jsx in package.json) — the only
 *     inline <script> tags are ones Next.js itself generates (which it
 *     nonces automatically, per its own docs) and the one hand-written
 *     inline <script> in app/layout.tsx (the pre-hydration theme
 *     bootstrap), which is nonced explicitly there. UNCHANGED by the
 *     style-src posture change below — no weakening here, ever.
 *
 *   style-src   'self' 'unsafe-inline'   (2026-08-14, deliberate posture
 *     change — was 'self' 'nonce-<per-request>', matching script-src)
 *     — CSP's `style-src` governs the literal `style=""` HTML ATTRIBUTE,
 *     and a nonce source, per spec, NEVER covers that attribute — only
 *     `'unsafe-inline'` (disabled the instant a nonce/hash is present in
 *     the same directive — CSP's backward-compat rule) or `'unsafe-hashes'`
 *     (a hash per exact string, impractical for a value that changes every
 *     render) can permit it. React's `style={{...}}` prop is CSP-safe
 *     client-side (React sets it via a JS property assignment, which CSP
 *     does not restrict) but react-dom/server has no live DOM to call that
 *     on, so SSR serializes every `style={{...}}` prop into a literal
 *     `style="..."` string attribute — and every route in this app is
 *     dynamically rendered (RootLayout's headers() call), so this is not a
 *     corner case. Measured directly against a real `next build && next
 *     start`: 10+ distinct style-src violations on an ordinary document
 *     detail page alone (see CLAUDE.md's "CORRECTION, 2026-08-13" note
 *     under the CSP section for the full mechanism). The nonce-only
 *     style-src was being violated on every authenticated page in
 *     production — a policy violated everywhere is not protecting
 *     anything, it is only noise that hides real console errors (it was
 *     the direct cause of one: a genuine React hydration failure on
 *     Settings → Connections got lost among 6+ style-src violation lines
 *     in the same console, 2026-08-14).
 *
 *     The fix removes the nonce from style-src ENTIRELY rather than adding
 *     `'unsafe-inline'` alongside it — per the backward-compat rule above,
 *     a nonce present in the same directive makes every nonce-aware
 *     browser ignore `'unsafe-inline'` outright, which would silently
 *     reproduce this exact bug. `'self'` is kept so the directive still
 *     says something (blocks a `<link rel="stylesheet">` to a foreign
 *     origin); `'unsafe-inline'` is what actually unblocks `style={{...}}`.
 *
 *     Cost, stated plainly: this makes CSS-injection possible on this
 *     origin where it was nominally blocked before — a real widening, and
 *     a much narrower attack surface than script injection (CSS alone
 *     cannot execute arbitrary JS or exfiltrate via fetch/XHR; the classic
 *     CSS-injection risks are content scraping via attribute selectors and
 *     UI redress, not code execution). It was not actually blocking
 *     anything in production anyway — the nonce-only policy was already
 *     failing open in effect, because it violated on every page without
 *     ever being enforced against a real attacker; this change trades
 *     theoretical protection nobody was getting for a console that reports
 *     real problems again.
 *
 *     THE PATH BACK: once every SSR-reachable `style={{...}}` call site
 *     (grep `style={{` across frontend/lib and frontend/app — ~666 hits at
 *     the time of this note, not triaged) is migrated to a CSS custom
 *     property set via a class name or a nonced `<style>` block instead of
 *     an inline attribute, style-src can retake the nonce and drop
 *     `'unsafe-inline'`, matching script-src again. That migration is a
 *     separate, large, cross-cutting change — not done here. Do not widen
 *     this further (no `'unsafe-hashes'`, no wildcard) and do not let
 *     `'unsafe-inline'` migrate into script-src, which keeps its nonce and
 *     `'strict-dynamic'` untouched.
 *
 *   img-src 'self' data: blob: https:
 *     — https: is a DELIBERATE, NARROW widening, not a default. Documents
 *     render arbitrary externally-hosted images
 *     (lib/workspace/fleet/markdown-lite.tsx's safeDocumentImageSrc allows
 *     any http(s) URL by design — that's the product, users paste image
 *     links into documents). data: is for the WhatsApp pairing QR code
 *     (lib/workspace/fleet/PersonalChannelConnectPanel.tsx generates it
 *     client-side via the `qrcode` package's toDataURL). blob: is for
 *     local file previews before upload
 *     (lib/workspace/fleet/DocumentDetailView.tsx's URL.createObjectURL).
 *
 *   connect-src 'self'
 *     — grepped the whole frontend for `new WebSocket(`: zero results.
 *     Every realtime surface (trace/notifications/channel-events/turn
 *     streams) uses EventSource, and resolveWorkspaceApiBaseUrl() always
 *     resolves to window.location.origin in the browser — there is no
 *     direct-from-browser call to any other origin, including the
 *     gateway/hardware pages (their WebSocket traffic is box<->cloud, not
 *     browser<->box). No wss:/ws: source is added because nothing in this
 *     app opens one; adding an unused source would be an unjustified
 *     widening of exactly the kind this file exists to avoid.
 *
 *   frame-src https:
 *     — the ONE real, narrowly-scoped exception in this policy.
 *     lib/workspace/hosted-mini-app-surface.tsx embeds a hosted mini-app's
 *     iframe at a publisher-controlled URL
 *     (manifest.hosted_app.hosted_url) that this app cannot enumerate in
 *     advance — that's the feature. default-src would otherwise block it
 *     entirely. Narrowed to https: only (never http:, never *), and the
 *     embed already carries its own defenses this policy doesn't
 *     duplicate: a `sandbox` attribute from the manifest, an `allow`
 *     allowlist from the manifest, and origin-checked postMessage
 *     (`allowedOrigins.has(event.origin)`) plus a launch-token bridge
 *     contract enforced server-side.
 *
 *   object-src 'none', base-uri 'self', form-action 'self',
 *   frame-ancestors 'none'
 *     — exactly the standing product law: the app is never legitimately
 *     framed, never plugin-embeds anything, and its own forms/base tag
 *     can't be redirected off-origin by an injected tag.
 *
 * 'unsafe-eval' is included ONLY when isDev is true. Next's own docs:
 * React uses eval() in development to reconstruct server-side error
 * stacks in the browser; neither React nor Next.js use eval in
 * production. Verified against a real `next build && next start` run
 * (see the test file's header) — no violation naming eval appeared.
 */

export const CSP_NONCE_REQUEST_HEADER = 'x-nonce';
export const CSP_RESPONSE_HEADER = 'Content-Security-Policy';

export interface BuildContentSecurityPolicyOptions {
  /** Per-request nonce, base64 of a fresh random value. Never reused. */
  nonce: string;
  /** True only for `NODE_ENV === 'development'`. Never true in a deployed build. */
  isDev: boolean;
}

/**
 * Directive list as data, not just the joined string — so the structural
 * test can assert on individual directives (e.g. "script-src has no
 * 'unsafe-inline'") without re-parsing a semicolon-joined string using the
 * same regex the implementation would use, which would let implementation
 * and test drift together silently.
 */
export function buildContentSecurityPolicyDirectives({
  nonce,
  isDev,
}: BuildContentSecurityPolicyOptions): Record<string, string[]> {
  if (!nonce || !nonce.trim()) {
    throw new Error('buildContentSecurityPolicyDirectives requires a non-empty per-request nonce.');
  }
  const nonceSource = `'nonce-${nonce}'`;

  return {
    'default-src': [`'self'`],
    'script-src': [`'self'`, nonceSource, `'strict-dynamic'`, ...(isDev ? [`'unsafe-eval'`] : [])],
    // No nonce here, deliberately, in prod AND dev alike — see the module
    // header's "style-src" entry (2026-08-14). A nonce alongside
    // 'unsafe-inline' makes every nonce-aware browser ignore
    // 'unsafe-inline' (CSP's own backward-compat rule), which would
    // silently reintroduce the exact bug this shape fixes. script-src is
    // untouched by this and keeps its nonce + 'strict-dynamic' in both modes.
    'style-src': [`'self'`, `'unsafe-inline'`],
    'img-src': [`'self'`, 'data:', 'blob:', 'https:'],
    'font-src': [`'self'`],
    'connect-src': [`'self'`],
    // Publisher-hosted mini-app embeds — see the module header. https: only.
    'frame-src': ['https:'],
    'object-src': [`'none'`],
    'base-uri': [`'self'`],
    'form-action': [`'self'`],
    'frame-ancestors': [`'none'`],
    'upgrade-insecure-requests': [],
  };
}

export function serializeContentSecurityPolicy(directives: Record<string, string[]>): string {
  return Object.entries(directives)
    .map(([name, values]) => (values.length > 0 ? `${name} ${values.join(' ')}` : name))
    .join('; ');
}

export function buildContentSecurityPolicy(options: BuildContentSecurityPolicyOptions): string {
  return serializeContentSecurityPolicy(buildContentSecurityPolicyDirectives(options));
}

/** Base64 of 16 random bytes — the shape Next.js's own docs use for nonces. */
export function generateNonce(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}
