/**
 * 2026-08-14 — Cloudflare's "Email Address Obfuscation" zone feature scans
 * every response body for something that *looks like* an email address and
 * rewrites it into `<a class="__cf_email__" data-cfemail="...">...</a>`,
 * AFTER Next.js has already rendered and sent the HTML. It works on the raw
 * response BYTES, not the DOM — so it hits anything email-shaped inside
 * that response: visible JSX text, but just as much the JSON embedded in
 * Next's own RSC "flight" payload (the `self.__next_f.push(...)` script
 * blocks that carry server-loaded data into Client Components). Whether or
 * not anything on screen actually shows the address is irrelevant.
 *
 * When that rewrite lands on data React is about to hydrate against, the
 * HTML React receives no longer matches what it rendered server-side, and
 * hydration fails outright (React error #418). React recovers by
 * client-rendering over the mismatch — the DOM looks fine and the bug
 * leaves no trace there — but every event handler on the page up to that
 * point never attaches. A dead "DigitalOcean" button in Settings with no
 * click, no navigation, no network request was the symptom that led here;
 * the cause was one line rendering the signed-in account's email in the
 * primary rail's owner row, plus the same field crossing into every page's
 * RSC payload regardless of whether it was ever displayed.
 *
 * Fix: a literal email address must never cross a server → client boundary
 * as plain text, in either place it can hide. This module is the one
 * choke point both server-side parsers (`account-shell-payload.ts`,
 * `workspace-bootstrap.ts`) route an account's email through before it
 * becomes a prop or gets serialized — see `obfuscateEmailForSsr`. The
 * companion `use-revealed-email.ts` hook is the only sanctioned way back to
 * the real value, and it is built so the decode can only ever happen
 * client-side, after hydration (see that file's own comment for why).
 *
 * The encoding is a fixed-key byte XOR rendered as hex — deliberately NOT
 * base64. The goal isn't secrecy (this is not encryption and provides no
 * confidentiality: the address still reaches the browser, just not in an
 * email-shaped string) — it's simply that the output must contain no "@"
 * and no "." anywhere, which any email-pattern scanner (Cloudflare's or
 * anyone else's, on anyone's CDN) keys on. A fixed-key XOR-to-hex output is
 * pure `[0-9a-f]`, guaranteeing that; base64's alphabet can still produce
 * strings a stricter scanner might flag, and more to the point needs a
 * different implementation server-side (Buffer) vs. client-side (btoa),
 * which this deliberately avoids by using nothing but string/char-code
 * arithmetic that behaves identically in Node and the browser.
 */
const SSR_EMAIL_XOR_KEY = 0x5a;

export function obfuscateEmailForSsr(email: string): string {
  let out = '';
  for (let i = 0; i < email.length; i += 1) {
    const code = email.charCodeAt(i) ^ SSR_EMAIL_XOR_KEY;
    out += code.toString(16).padStart(4, '0');
  }
  return out;
}

export function revealEmail(obfuscated: string): string {
  if (!obfuscated || obfuscated.length % 4 !== 0) {
    return '';
  }
  let out = '';
  for (let i = 0; i < obfuscated.length; i += 4) {
    const hex = obfuscated.slice(i, i + 4);
    if (!/^[0-9a-f]{4}$/i.test(hex)) {
      return '';
    }
    const code = parseInt(hex, 16) ^ SSR_EMAIL_XOR_KEY;
    out += String.fromCharCode(code);
  }
  return out;
}
