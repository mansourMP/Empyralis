/**
 * Markdown → native channel formatting.
 *
 * Agent replies arrive as markdown (bold/italic/inline-code/fenced-code/
 * strikethrough/links). Sent verbatim they show literal `**asterisks**` and
 * backticks, which reads robotic. This module renders that markdown into each
 * channel's native format on the way out:
 *
 *   - Telegram: HTML for `parse_mode: "html"` — <b>/<i>/<s>/<code>/<pre>/<a>.
 *     Every non-code character is HTML-escaped, so text that ISN'T valid
 *     markdown still produces valid HTML. renderMarkdownToTelegramHtml also
 *     reports whether any formatting was actually applied: when nothing was,
 *     the caller sends the ORIGINAL text with no parse_mode (byte-identical to
 *     the pre-formatting behavior, and no escaping of an innocuous "<").
 *   - WhatsApp: its own inline conventions — *bold*, _italic_, ~strike~, and
 *     ```monospace``` — which are literal characters in the message body, so
 *     there is no parse mode and a stray marker degrades to a visible
 *     character rather than a send error.
 *
 * The parser is deliberately conservative (it protects code spans first, then
 * applies a small set of well-anchored inline rules) so it rarely mis-fires on
 * prose; and for Telegram the send path has a final belt-and-suspenders
 * fallback to plain text if the platform ever rejects the generated HTML.
 */

export type TelegramParseMode = "html";

export interface TelegramMarkdownRender {
  /** The text to place on the wire: generated HTML when `formatted`, else the
   *  original markdown untouched. */
  text: string;
  /** Set only when `formatted` — hand straight to GramJS's `parseMode`. */
  parseMode?: TelegramParseMode;
  /** True when at least one markdown construct was rendered. When false the
   *  caller should send `text` (== the original) with no parse mode. */
  formatted: boolean;
}

type Segment =
  | { kind: "text"; value: string }
  | { kind: "inlineCode"; value: string }
  | { kind: "codeBlock"; value: string };

const FENCED_CODE_RE = /```[^\n`]*\n?([\s\S]*?)```/g;
const INLINE_CODE_RE = /`([^`\n]+)`/g;

/**
 * Splits markdown into text / inline-code / fenced-code segments so the inline
 * formatters never reach inside a code span (where `*` and `_` are literal).
 * Fenced blocks are extracted first (they can span lines), then inline code is
 * carved out of the remaining text runs.
 */
export function segmentMarkdown(md: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  FENCED_CODE_RE.lastIndex = 0;
  while ((match = FENCED_CODE_RE.exec(md)) !== null) {
    if (match.index > lastIndex) {
      pushTextWithInlineCode(md.slice(lastIndex, match.index), segments);
    }
    segments.push({ kind: "codeBlock", value: match[1] ?? "" });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < md.length) {
    pushTextWithInlineCode(md.slice(lastIndex), segments);
  }
  return segments;
}

function pushTextWithInlineCode(text: string, segments: Segment[]): void {
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  INLINE_CODE_RE.lastIndex = 0;
  while ((match = INLINE_CODE_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ kind: "text", value: text.slice(lastIndex, match.index) });
    }
    segments.push({ kind: "inlineCode", value: match[1] ?? "" });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    segments.push({ kind: "text", value: text.slice(lastIndex) });
  }
}

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
};

function escapeHtml(text: string): string {
  return text.replace(/[&<>"]/g, (char) => HTML_ESCAPES[char] ?? char);
}

// Inline rules, applied outside code spans. Order matters: links first (they
// contain the [] () that would otherwise confuse emphasis), then bold before
// italic (so `**x**` isn't eaten as two italics), then strikethrough. Emphasis
// captures require a non-space at each inner edge to avoid matching "a * b".
const LINK_RE = /\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)/g;
const BOLD_RE = /\*\*(\S(?:[^*]*?\S)?)\*\*/g;
const STRIKE_RE = /~~(\S(?:[^~]*?\S)?)~~/g;
const ITALIC_STAR_RE = /\*(\S(?:[^*]*?\S)?)\*/g;
// Underscore emphasis only outside a word (so file_name_here / a_b stay put).
const ITALIC_UNDERSCORE_RE = /(^|[^\w*])_(\S(?:[^_]*?\S)?)_(?![\w])/g;

function escapeHtmlAttr(url: string): string {
  return url.replace(/[&<>"]/g, (char) => HTML_ESCAPES[char] ?? char);
}

/** Collects "already-converted" spans behind sentinel placeholders so a later
 *  inline rule can't re-match a marker the previous rule just emitted (the
 *  classic `**bold**` → `*bold*` → `_bold_` collision on WhatsApp, where bold
 *  and italic share the `*`). */
class SpanVault {
  private readonly spans: string[] = [];
  hold(value: string): string {
    this.spans.push(value);
    return `\u0000${this.spans.length - 1}\u0000`;
  }
  restore(text: string): string {
    return text.replace(/\u0000(\d+)\u0000/g, (_m, index: string) => this.spans[Number(index)] ?? "");
  }
}

/** Applies the inline rules to an already-HTML-escaped text run, returning the
 *  HTML and whether anything matched. Converted spans (links/bold/strike) are
 *  vaulted so a following rule cannot reinterpret their markup. */
function applyInlineHtml(escaped: string): { html: string; changed: boolean } {
  let changed = false;
  const vault = new SpanVault();
  let out = escaped.replace(LINK_RE, (_m, label: string, url: string) => {
    changed = true;
    return vault.hold(`<a href="${escapeHtmlAttr(url)}">${label}</a>`);
  });
  out = out.replace(BOLD_RE, (_m, inner: string) => {
    changed = true;
    return vault.hold(`<b>${inner}</b>`);
  });
  out = out.replace(STRIKE_RE, (_m, inner: string) => {
    changed = true;
    return vault.hold(`<s>${inner}</s>`);
  });
  out = out.replace(ITALIC_STAR_RE, (_m, inner: string) => {
    changed = true;
    return `<i>${inner}</i>`;
  });
  out = out.replace(ITALIC_UNDERSCORE_RE, (_m, lead: string, inner: string) => {
    changed = true;
    return `${lead}<i>${inner}</i>`;
  });
  return { html: vault.restore(out), changed };
}

/** Applies the inline rules to a raw text run, emitting WhatsApp's native
 *  markers. WhatsApp has no link markup, so a link becomes "label (url)".
 *  Because WhatsApp bold (`*`) and italic (`_`) would otherwise collide (bold
 *  emits `*x*`, which the italic rule then eats), converted spans are vaulted
 *  behind placeholders until every rule has run. */
function applyInlineWhatsApp(text: string): string {
  const vault = new SpanVault();
  let out = text.replace(LINK_RE, (_m, label: string, url: string) => vault.hold(`${label} (${url})`));
  out = out.replace(BOLD_RE, (_m, inner: string) => vault.hold(`*${inner}*`));
  out = out.replace(STRIKE_RE, (_m, inner: string) => vault.hold(`~${inner}~`));
  out = out.replace(ITALIC_STAR_RE, (_m, inner: string) => vault.hold(`_${inner}_`));
  out = out.replace(ITALIC_UNDERSCORE_RE, (_m, lead: string, inner: string) => `${lead}${vault.hold(`_${inner}_`)}`);
  return vault.restore(out);
}

/**
 * Renders markdown to Telegram HTML. Returns `{ formatted: false, text: md }`
 * unchanged when the input carries no markdown, so plain messages send exactly
 * as they did before (no parse mode, no escaping).
 */
export function renderMarkdownToTelegramHtml(md: string): TelegramMarkdownRender {
  const source = typeof md === "string" ? md : "";
  const segments = segmentMarkdown(source);
  let formatted = false;
  const parts: string[] = [];
  for (const segment of segments) {
    if (segment.kind === "codeBlock") {
      formatted = true;
      parts.push(`<pre>${escapeHtml(segment.value)}</pre>`);
    } else if (segment.kind === "inlineCode") {
      formatted = true;
      parts.push(`<code>${escapeHtml(segment.value)}</code>`);
    } else {
      const { html, changed } = applyInlineHtml(escapeHtml(segment.value));
      if (changed) {
        formatted = true;
      }
      parts.push(html);
    }
  }
  if (!formatted) {
    return { text: source, formatted: false };
  }
  return { text: parts.join(""), parseMode: "html", formatted: true };
}

/**
 * Renders markdown to WhatsApp's native inline formatting. Always returns a
 * string (there is no parse mode); input with no markdown comes back
 * unchanged.
 */
export function renderMarkdownToWhatsApp(md: string): string {
  const source = typeof md === "string" ? md : "";
  const segments = segmentMarkdown(source);
  const parts: string[] = [];
  for (const segment of segments) {
    if (segment.kind === "codeBlock") {
      // WhatsApp monospace uses the same triple-backtick fence; normalize the
      // trailing newline so the closing fence sits on its own clean line.
      parts.push("```\n" + segment.value.replace(/\n+$/, "") + "\n```");
    } else if (segment.kind === "inlineCode") {
      // WhatsApp has no single-backtick inline code; triple-backtick renders
      // the span monospace inline.
      parts.push("```" + segment.value + "```");
    } else {
      parts.push(applyInlineWhatsApp(segment.value));
    }
  }
  return parts.join("");
}
