/**
 * THE SINGLE MOST IMPORTANT LINE IN THE DESKTOP PAIRING FLOW.
 *
 * The desktop app opens the customer's own browser at `/desktop/pair`,
 * carrying the address of a loopback listener it just bound. That page signs
 * the customer in, mints a short-lived pairing token, and REDIRECTS TO
 * WHATEVER ADDRESS IT WAS HANDED. Without this module that is an open
 * redirect with a credential attached: anyone who can get a person to open
 * `https://empyralis.ai/desktop/pair?callback=https://evil.example/…` gets a
 * live pairing token for that person's workspace delivered to their own
 * server, from an authenticated session, with the customer seeing nothing
 * more alarming than our own page.
 *
 * So: the callback is only ever a LOOPBACK address on this machine, and the
 * check is an allowlist rather than a denylist. A denylist ("not evil.example",
 * "no external hosts") is the wrong shape here — the set of URLs that are
 * safe is tiny and enumerable, and the set that is not is infinite.
 *
 * ── Why the whole URL is pinned, not just the host ───────────────────────
 * The desktop shell builds this address itself from a port it just bound
 * (src-tauri/src/browser_pairing.rs's `pairing_page_url`), so a legitimate
 * callback has exactly one shape. Anything else — an https scheme, a path
 * that is not the callback path, a query already attached, a fragment, a
 * userinfo section — is not a callback this product produces, and accepting
 * one would mean accepting something built by someone else.
 *
 * `localhost` is accepted alongside `127.0.0.1` because it is the other name
 * for the same interface and RFC 8252 names both. Note that it is NOT merely
 * a nicety that both are allowed and nothing else is: `localhost.evil.example`
 * and `127.0.0.1.evil.example` are ordinary public hostnames, and a
 * `startsWith`/`includes` check on either name would hand a token straight to
 * them. This compares the parsed hostname for EQUALITY.
 *
 * ── PARSE FIRST, THEN COMPARE. Measured, not assumed. ────────────────────
 * WHATWG `new URL()` canonicalises obfuscated IPv4 literals before
 * `hostname` can be read: `0177.0.0.1` (octal), `2130706433` (decimal) and
 * `127.1` (short form) all come out as the string `127.0.0.1`. That is what
 * makes an equality check on `hostname` complete rather than a set of three
 * bypasses waiting to be found. It is also why this must never be
 * "simplified" into a regex over the raw string: that shortcut refuses three
 * legitimate addresses AND accepts `http://127.0.0.1.evil.example`, which is
 * the exact hazard this module exists for.
 *
 * ── Refusing is silent about nothing ─────────────────────────────────────
 * A refusal never mints. The page renders the refusal rather than falling
 * back to "connect anyway with no callback" — a pairing that half-happens is
 * exactly the outcome-honesty failure CLAUDE.md names, and here the half that
 * happened would be a live token in a workspace for a computer nobody
 * connected.
 */

/** The path the desktop shell listens on. Pinned; mirrors
 *  `CALLBACK_PATH` in src-tauri/src/browser_pairing.rs. */
export const DESKTOP_PAIR_CALLBACK_PATH = '/desktop-pair/callback';

/** The only two names for this machine's own loopback interface that this
 *  product ever produces. Equality, never prefix or substring. */
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost']);

export type CallbackCheck =
  | { ok: true; callback: string }
  | { ok: false; reason: string };

/**
 * A `state` nonce is 32 CSPRNG bytes, base64url — so the only characters that
 * can legitimately appear are the base64url alphabet, and the length is
 * knowable. Constrained rather than passed through because this value is
 * echoed back into a URL: an unconstrained one is a place to hide a payload.
 */
export function isValidPairState(raw: unknown): boolean {
  const value = typeof raw === 'string' ? raw.trim() : '';
  return /^[A-Za-z0-9_-]{16,128}$/.test(value);
}

/**
 * Decides whether a `?callback=` may be redirected to. See the module doc
 * comment — this is the gate the whole flow's security rests on.
 */
export function checkPairCallback(raw: unknown): CallbackCheck {
  const value = typeof raw === 'string' ? raw.trim() : '';
  if (!value) {
    return { ok: false, reason: 'missing' };
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return { ok: false, reason: 'unparseable' };
  }

  // http only. Our listener speaks plain http on loopback (which browsers
  // treat as a secure context precisely so this pattern works); an https
  // callback is not something this product ever builds.
  if (url.protocol !== 'http:') {
    return { ok: false, reason: 'scheme' };
  }
  // EQUALITY. `localhost.evil.example` and `127.0.0.1.evil.example` are
  // ordinary public hostnames that any looser check would accept.
  if (!LOOPBACK_HOSTS.has(url.hostname)) {
    return { ok: false, reason: 'host' };
  }
  // `http://someone@127.0.0.1:1234/` parses to this host, so the userinfo has
  // to be refused explicitly rather than assumed absent.
  if (url.username || url.password) {
    return { ok: false, reason: 'userinfo' };
  }
  const port = Number(url.port);
  if (!url.port || !Number.isInteger(port) || port < 1 || port > 65535) {
    return { ok: false, reason: 'port' };
  }
  if (url.pathname !== DESKTOP_PAIR_CALLBACK_PATH) {
    return { ok: false, reason: 'path' };
  }
  // The parameters this page attaches are the only ones that belong on this
  // URL. Anything already there was put there by whoever wrote the link.
  if (url.search || url.hash) {
    return { ok: false, reason: 'extras' };
  }

  // Rebuilt from the parsed parts rather than returned verbatim, so what is
  // navigated to is what was actually checked.
  return { ok: true, callback: `http://${url.hostname}:${port}${DESKTOP_PAIR_CALLBACK_PATH}` };
}

/**
 * The URL the page navigates to once the customer approves.
 *
 * The token travels as a query parameter because that is RFC 8252's loopback
 * redirect and the only shape available here: a form POST would be blocked by
 * this app's own `form-action 'self'` CSP, and `fetch` by `connect-src 'self'`.
 * It never leaves this machine, it is single-use, and the listener's response
 * page strips it from the address bar with `history.replaceState` as soon as
 * it is spent.
 *
 * Throws rather than returning a degraded URL on an unchecked callback: a
 * caller that has not run `checkPairCallback` has a bug, and quietly building
 * something anyway is how that bug reaches a customer.
 */
export function buildPairCallbackUrl(
  callback: string,
  params: {
    state: string;
    pairingToken: string;
    workspaceId: string;
    workspaceLabel?: string;
  },
): string {
  const checked = checkPairCallback(callback);
  if (!checked.ok) {
    throw new Error(`Refusing to build a callback for a non-loopback address (${checked.reason}).`);
  }
  const url = new URL(checked.callback);
  url.searchParams.set('state', params.state);
  url.searchParams.set('pairing_token', params.pairingToken);
  url.searchParams.set('workspace_id', params.workspaceId);
  if (params.workspaceLabel?.trim()) {
    url.searchParams.set('workspace_label', params.workspaceLabel.trim());
  }
  return url.toString();
}

/**
 * The URL the page navigates to when the customer says no.
 *
 * Told rather than left to time out, because "they said no" and "the browser
 * never came back" are different facts that send a person to do different
 * things — and the app cannot tell them apart on its own.
 */
export function buildPairDenialUrl(callback: string, state: string): string {
  const checked = checkPairCallback(callback);
  if (!checked.ok) {
    throw new Error(`Refusing to build a callback for a non-loopback address (${checked.reason}).`);
  }
  const url = new URL(checked.callback);
  url.searchParams.set('state', state);
  url.searchParams.set('error', 'denied');
  return url.toString();
}

/** Plain sentence for a refused callback. Never echoes the address back —
 *  this is rendered, and the address came from whoever wrote the link. */
export function pairCallbackRefusalDetail(reason: string): string {
  if (reason === 'missing') {
    return "This page is opened by the Empyralis desktop app. It doesn't do anything on its own.";
  }
  return "This link didn't come from the Empyralis app on this computer, so nothing was connected. Open the Empyralis app and press Connect there.";
}
