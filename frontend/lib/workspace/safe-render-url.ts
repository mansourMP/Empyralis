/**
 * Shared URL allowlist for render seams that turn a value from data — an
 * upload response persisted on a turn, a websocket event, an API payload —
 * straight into a live `href`/`src`, outside the two markdown renderers that
 * already carry their own copy of this same guard
 * (lib/workspace/markdown-lite.tsx's safeMarkdownLiteHref,
 * lib/workspace/fleet/markdown-lite.tsx's safeDocumentHref/
 * safeDocumentImageSrc). Those two are left as-is — each is already shipped,
 * tested, and covered by its own file's test — this module exists so a
 * THIRD or FOURTH sink does not each grow its own copy, or worse, skip the
 * guard entirely because "it's just a data field, not markdown".
 *
 * Same allowlist, same reasoning as the markdown renderers: absolute
 * http/https (plus mailto for links, never for images) and same-origin
 * relative paths/anchors (leading `/`, never the protocol-relative `//host`
 * form, which silently changes host). Anything else — javascript:, data:,
 * vbscript:, a bare scheme-less string — returns null so the caller renders
 * inert text instead of a live href/src.
 */

const SAFE_LINK_SCHEMES = new Set(["http:", "https:", "mailto:"]);
const SAFE_IMAGE_SCHEMES = new Set(["http:", "https:"]);
const SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;

/** Strips characters browsers ignore when sniffing a scheme (tabs,
 *  newlines) -- "java\tscript:" is a classic filter-bypass trick -- then
 *  trims surrounding whitespace. */
function cleanUrl(url: string): string {
  return String(url ?? "").replace(/[\t\n\r]/g, "").trim();
}

/** For an `<a href>`: absolute http(s), mailto, a same-origin relative path
 *  (leading `/`, not `//`), or a bare `#anchor`. Everything else — including
 *  javascript:/data:/vbscript: and protocol-relative `//host` — returns
 *  null, so the caller must render inert text instead of a live link. */
export function safeExternalHref(url: string | null | undefined): string | null {
  const cleaned = cleanUrl(url || "");
  if (!cleaned) return null;
  if (cleaned.startsWith("#")) return cleaned;
  if (cleaned.startsWith("/") && !cleaned.startsWith("//")) return cleaned;
  const schemeMatch = SCHEME_RE.exec(cleaned);
  if (schemeMatch && SAFE_LINK_SCHEMES.has(`${schemeMatch[1].toLowerCase()}:`)) {
    return cleaned;
  }
  return null;
}

/** For an `<img src>`: absolute http(s) or a same-origin relative path.
 *  mailto is not an image scheme, so it is refused here even though
 *  safeExternalHref allows it for links. */
export function safeExternalImageSrc(url: string | null | undefined): string | null {
  const cleaned = cleanUrl(url || "");
  if (!cleaned) return null;
  if (cleaned.startsWith("/") && !cleaned.startsWith("//")) return cleaned;
  const schemeMatch = SCHEME_RE.exec(cleaned);
  if (schemeMatch && SAFE_IMAGE_SCHEMES.has(`${schemeMatch[1].toLowerCase()}:`)) {
    return cleaned;
  }
  return null;
}
