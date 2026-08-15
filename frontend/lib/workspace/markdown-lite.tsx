/**
 * THE markdown renderer for this app. One implementation, two VARIANTS —
 * never two implementations.
 *
 * There used to be two files called "markdown-lite": this one (chat turns:
 * bold/italic/code/links and flat lists, nothing else) and
 * `lib/workspace/fleet/markdown-lite.tsx` (project documents: headings,
 * tables, fenced code, blockquotes, images, nested lists). Chat imported the
 * weak one, so an agent that answered with a perfectly good `## Report`, a
 * pipe table and a ```python block rendered the literal characters
 * `## Report`, `| Name | Value |` and ```` ```python ```` as text while its
 * bullet list and **bold** worked — the same construct arriving as either
 * real markup or literal punctuation depending on which surface you were
 * looking at. That file is gone; this is what both surfaces call now.
 *
 * SAFE BY CONSTRUCTION: every block below is built as real React elements
 * from parsed text, never `dangerouslySetInnerHTML` — there is no HTML
 * parsing step for a malicious `<script>` or `<img onerror>` to survive. A
 * literal "<div>" in a message or a document renders as the five visible
 * characters "<div>", not a tag.
 *
 * Supported: `#`..`######` headings, `**bold**`/`__bold__`,
 * `*italic*`/`_italic_`, `` `inline code` ``, fenced code blocks (the
 * language tag is preserved on the element, see renderBlock — there is no
 * syntax highlighter here), `[text](url)` links, `![alt](url)` images
 * (DOCUMENTS ONLY — see the variant table below), GitHub-flavoured pipe
 * tables (header row, `:--`/`:-:`/`--:` alignment row, body rows — rendered
 * as real <table>/<thead>/<tbody>/<th>/<td>, not divs), `-`/`*`/`+`
 * unordered and `1.` ordered lists (with indentation-based nesting, any
 * depth), `>` blockquotes, `---` horizontal rules, and paragraphs whose
 * single newlines render as soft breaks (so text typed without
 * blank-line-per-paragraph discipline still reads the way it was typed).
 * NOT supported: footnotes, raw HTML, reference-style links. Text using one
 * of those still renders — the unsupported markup just prints as literal
 * characters instead of being interpreted.
 *
 * ── THE TWO VARIANTS, AND THE ONE AXIS THEY DIFFER ON ────────────────────
 *
 *                       MarkdownLite (documents)   MarkdownLiteText (chat,
 *                                                  task descriptions,
 *                                                  conversation transcripts)
 *   images               rendered                  NEVER rendered
 *   heading levels       demoted one (# -> h2)     literal (# -> h1)
 *   class family         fleet-doc-*               fleet-md-*
 *   link/href policy     IDENTICAL — safeMarkdownLiteHref, one function
 *
 * IMAGES ARE THE SECURITY DIFFERENCE AND THE ONLY ONE THAT IS NOT COSMETIC.
 * A project document is written by a workspace member; a chat turn can carry
 * text an EXTERNAL STRANGER sent the agent over a channel (WhatsApp,
 * Telegram, an OpenClaw-transported channel), relayed into the owner's
 * browser. An `<img src="https://attacker.example/1x1.png">` is a zero-click
 * request from the owner's browser to a host the stranger chose: it hands
 * over the owner's IP and User-Agent and confirms the message was read, with
 * no interaction at all. The app's CSP deliberately allows `img-src https:`
 * (see lib/security/content-security-policy.ts — it was widened FOR
 * documents, where an owner pasting an external image link is the feature),
 * so CSP will not stop it either. Documents keep images because the person
 * who typed the URL is the person looking at the page; chat does not, and a
 * refused image renders its alt text as inert characters. Do not "finish the
 * job" by turning images on for chat.
 *
 * Heading levels are the cosmetic difference: a document's body sits under
 * the page's own <h1> document title in the same column, so its `# Title`
 * becomes an h2 and its `## Sub` an h3 (the "one real heading per level"
 * discipline the document page already followed). A chat turn is an
 * <article> — HTML sectioning content — so its author's heading levels are
 * rendered literally, which is also what every chat client does with a
 * markdown reply.
 *
 * LINK/IMAGE URL SAFETY: a `[text](url)` or `![alt](url)` never puts `url`
 * verbatim into `href`/`src` — see safeMarkdownLiteHref/
 * safeMarkdownLiteImageSrc below. Both refuse `javascript:`/`data:`/any
 * scheme outside an explicit allowlist and protocol-relative `//host` links
 * (which silently change host); a refused URL falls back to inert text
 * instead of a live href/src. Images additionally refuse anything resolving
 * to a `.svg` path — see CLAUDE.md's upload-policy note: attachments are
 * served straight back by FileResponse, so an SVG is a picture that is also
 * a program on the workspace's own origin, and this renderer must not give
 * one a path to execute as an <img>.
 */

import { Fragment, type CSSProperties, type ReactNode } from "react";

type Align = "left" | "center" | "right" | null;

type ListItemNode = { text: string; children?: ListBlock[] };
type ListBlock = { kind: "list"; ordered: boolean; items: ListItemNode[] };

export type MarkdownLiteBlock =
  | { kind: "heading"; level: 1 | 2 | 3 | 4 | 5 | 6; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "code"; lang: string; code: string }
  | { kind: "quote"; lines: string[] }
  | ListBlock
  | { kind: "table"; header: string[]; align: Align[]; rows: string[][] }
  | { kind: "hr" };

const HR_RE = /^(?:-{3,}|\*{3,}|_{3,})$/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const QUOTE_RE = /^>\s?(.*)$/;
const FENCE_RE = /^```\s*([\w-]*)\s*$/;
const UL_MARKER_RE = /^( *)[-*+]\s+(.*)$/;
const OL_MARKER_RE = /^( *)\d+\.\s+(.*)$/;
const ALIGN_CELL_RE = /^:?-{1,}:?$/;

/** Leading tabs (only) are expanded to 4 spaces so list-nesting indentation
 *  can be measured consistently — the same "tab stop" convention CommonMark
 *  uses. Interior tabs (inside a line's own text) are left untouched. */
function expandLeadingTabs(line: string): string {
  let i = 0;
  while (i < line.length && line[i] === "\t") i += 1;
  if (i === 0) return line;
  return "    ".repeat(i) + line.slice(i);
}

function matchListMarker(line: string): { indent: number; ordered: boolean; content: string } | null {
  const ul = UL_MARKER_RE.exec(line);
  if (ul) return { indent: ul[1].length, ordered: false, content: ul[2] };
  const ol = OL_MARKER_RE.exec(line);
  if (ol) return { indent: ol[1].length, ordered: true, content: ol[2] };
  return null;
}

/** Splits a pipe-delimited table row into trimmed cells, honouring a
 *  leading/trailing pipe (optional, per GFM) and `\|` as an escaped literal
 *  pipe inside a cell. */
function splitTableRow(line: string): string[] {
  const trimmed = line.trim();
  const stripped = trimmed.replace(/^\|/, "").replace(/\|$/, "");
  const cells: string[] = [];
  let current = "";
  for (let idx = 0; idx < stripped.length; idx += 1) {
    const ch = stripped[idx];
    if (ch === "\\" && stripped[idx + 1] === "|") {
      current += "|";
      idx += 1;
      continue;
    }
    if (ch === "|") {
      cells.push(current.trim());
      current = "";
      continue;
    }
    current += ch;
  }
  cells.push(current.trim());
  return cells;
}

/** Returns the per-column alignment for a `| --- | :--: | ---: |` row, or
 *  null if `line` isn't a valid GFM alignment row at all (the signal used
 *  to decide whether the preceding line was a table header). */
function parseAlignmentRow(line: string): Align[] | null {
  if (!line.includes("-")) return null;
  const cells = splitTableRow(line);
  if (cells.length === 0) return null;
  const aligns: Align[] = [];
  for (const cell of cells) {
    if (!ALIGN_CELL_RE.test(cell)) return null;
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) aligns.push("center");
    else if (right) aligns.push("right");
    else if (left) aligns.push("left");
    else aligns.push(null);
  }
  return aligns;
}

/** Parses one (possibly multi-level) list starting at `lines[i]`, whose
 *  first item is known to sit at `indent` spaces. A sibling item at a
 *  SHALLOWER indent, or of the other marker type, ends this list; a
 *  sibling item at a DEEPER indent immediately after an item is that
 *  item's nested sub-list, parsed recursively. */
function parseList(lines: string[], start: number, indent: number): { block: ListBlock; next: number } {
  const first = matchListMarker(lines[start])!;
  const ordered = first.ordered;
  const items: ListItemNode[] = [];
  let i = start;

  while (i < lines.length) {
    if (lines[i].trim() === "") {
      let j = i;
      while (j < lines.length && lines[j].trim() === "") j += 1;
      if (j >= lines.length) {
        i = j;
        break;
      }
      const peeked = matchListMarker(lines[j]);
      if (peeked && peeked.indent >= indent) {
        i = j;
        continue;
      }
      break;
    }

    const marker = matchListMarker(lines[i]);
    if (!marker || marker.indent < indent || marker.ordered !== ordered) break;
    if (marker.indent > indent) break; // a deeper item not immediately following its parent belongs to an outer frame

    i += 1;
    let children: ListBlock[] | undefined;

    let lookahead = i;
    while (lookahead < lines.length && lines[lookahead].trim() === "") lookahead += 1;
    if (lookahead < lines.length) {
      const nested = matchListMarker(lines[lookahead]);
      if (nested && nested.indent > indent) {
        const sub = parseList(lines, lookahead, nested.indent);
        children = [sub.block];
        i = sub.next;
      }
    }

    items.push({ text: marker.content, children });
  }

  return { block: { kind: "list", ordered, items }, next: i };
}

function isTableStart(lines: string[], i: number): boolean {
  if (i + 1 >= lines.length) return false;
  if (!lines[i].includes("|")) return false;
  return parseAlignmentRow(lines[i + 1]) !== null;
}

/** The one block parser. Exported because MemoryTab.tsx builds its own
 *  (deliberately different) preview presentation for memory files on top of
 *  the same block tree — one parser, several presentations, never a second
 *  parser. */
export function parseMarkdownLiteBlocks(markdown: string): MarkdownLiteBlock[] {
  const lines = String(markdown || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map(expandLeadingTabs);
  const blocks: MarkdownLiteBlock[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i += 1;
      continue;
    }

    const fence = FENCE_RE.exec(line);
    if (fence) {
      const lang = fence[1] || "";
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE_RE.test(lines[i]) && lines[i].trim() !== "```") {
        codeLines.push(lines[i]);
        i += 1;
      }
      // Consume the closing fence (or end of input, for an unterminated block).
      if (i < lines.length) i += 1;
      blocks.push({ kind: "code", lang, code: codeLines.join("\n") });
      continue;
    }

    if (HR_RE.test(line.trim())) {
      blocks.push({ kind: "hr" });
      i += 1;
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length as 1 | 2 | 3 | 4 | 5 | 6, text: heading[2] });
      i += 1;
      continue;
    }

    const quoteMatch = QUOTE_RE.exec(line);
    if (quoteMatch) {
      const quoteLines: string[] = [quoteMatch[1]];
      i += 1;
      while (i < lines.length) {
        const m = QUOTE_RE.exec(lines[i]);
        if (!m) break;
        quoteLines.push(m[1]);
        i += 1;
      }
      blocks.push({ kind: "quote", lines: quoteLines });
      continue;
    }

    if (isTableStart(lines, i)) {
      const header = splitTableRow(line);
      const align = parseAlignmentRow(lines[i + 1])!;
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].includes("|")) {
        rows.push(splitTableRow(lines[i]));
        i += 1;
      }
      blocks.push({ kind: "table", header, align, rows });
      continue;
    }

    const listMarker = matchListMarker(line);
    if (listMarker) {
      const { block, next } = parseList(lines, i, listMarker.indent);
      blocks.push(block);
      i = next;
      continue;
    }

    // Paragraph: consecutive non-blank lines that didn't match anything else.
    const paraLines: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !FENCE_RE.test(lines[i]) &&
      !HR_RE.test(lines[i].trim()) &&
      !HEADING_RE.test(lines[i]) &&
      !QUOTE_RE.test(lines[i]) &&
      !matchListMarker(lines[i]) &&
      !isTableStart(lines, i)
    ) {
      paraLines.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: "paragraph", lines: paraLines });
  }

  return blocks;
}

// ─── URL safety ──────────────────────────────────────────────────────────
//
// This renderer turns a parsed `[text](url)`/`![alt](url)` straight into a
// real `<a href>`/`<img src>` — so text containing `[click me](javascript:...)`
// or `![x](javascript:...)` must never reach that attribute unvalidated.
// Allow only absolute http/https (plus mailto for links) and same-origin
// relative paths/anchors (leading `/` — but not the protocol-relative
// `//host` form, which silently changes host — or `#`); anything else
// (javascript:, data:, vbscript:, a bare scheme-less string, ...) returns
// null so the caller renders the visible text as inert plain text instead
// of a live href/src.

const SAFE_LINK_SCHEMES = new Set(["http:", "https:", "mailto:"]);
const SAFE_IMAGE_SCHEMES = new Set(["http:", "https:"]);
const SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;

function cleanUrl(url: string): string {
  // Strip characters browsers ignore when sniffing a scheme (tabs,
  // newlines) -- "java\tscript:" is a classic filter-bypass trick -- then
  // trim surrounding whitespace.
  return url.replace(/[\t\n\r]/g, "").trim();
}

function safeMarkdownLiteHref(url: string): string | null {
  const cleaned = cleanUrl(url);
  if (!cleaned) return null;
  if (cleaned.startsWith("#")) return cleaned;
  if (cleaned.startsWith("/") && !cleaned.startsWith("//")) return cleaned;
  const schemeMatch = SCHEME_RE.exec(cleaned);
  if (schemeMatch && SAFE_LINK_SCHEMES.has(`${schemeMatch[1].toLowerCase()}:`)) {
    return cleaned;
  }
  return null;
}

/** True if `url`'s path (ignoring query/hash) ends in `.svg` — see the file
 *  header: an SVG is a picture that is also a program on this app's own
 *  origin, so this renderer must never give one an <img> path. */
function hasSvgExtension(url: string): boolean {
  const path = url.split("#")[0].split("?")[0];
  return /\.svg$/i.test(path);
}

function safeMarkdownLiteImageSrc(url: string): string | null {
  const cleaned = cleanUrl(url);
  if (!cleaned) return null;
  if (hasSvgExtension(cleaned)) return null;
  if (cleaned.startsWith("/") && !cleaned.startsWith("//")) return cleaned;
  const schemeMatch = SCHEME_RE.exec(cleaned);
  if (schemeMatch && SAFE_IMAGE_SCHEMES.has(`${schemeMatch[1].toLowerCase()}:`)) {
    return cleaned;
  }
  return null;
}

// ─── Variants ────────────────────────────────────────────────────────────

type MarkdownLiteTheme = {
  paragraph: string;
  heading: string;
  list: string;
  inlineCode: string;
  codeBlock: string;
  quote: string;
  tableWrap: string;
  table: string;
  hr: string;
  image: string;
  /** "" renders no className on the <a> at all (documents style anchors
   *  through their body's own descendant selector). */
  link: string;
};

type MarkdownLiteVariant = {
  theme: MarkdownLiteTheme;
  /** Added to every source heading level before rendering, capped at h6. */
  headingLevelOffset: 0 | 1;
  /** See the file header: false is a SECURITY decision for chat, not a
   *  feature gap. */
  allowImages: boolean;
};

const DOCUMENT_VARIANT: MarkdownLiteVariant = {
  theme: {
    paragraph: "fleet-doc-paragraph",
    heading: "fleet-doc-heading",
    list: "fleet-doc-list",
    inlineCode: "fleet-doc-inline-code",
    codeBlock: "fleet-doc-code",
    quote: "fleet-doc-quote",
    tableWrap: "fleet-doc-table-wrap",
    table: "fleet-doc-table",
    hr: "fleet-doc-hr",
    image: "fleet-doc-image",
    link: "",
  },
  headingLevelOffset: 1,
  allowImages: true,
};

const COMPACT_VARIANT: MarkdownLiteVariant = {
  theme: {
    paragraph: "fleet-md-p",
    heading: "fleet-md-heading",
    list: "fleet-md-list",
    inlineCode: "fleet-md-code",
    codeBlock: "fleet-md-pre",
    quote: "fleet-md-quote",
    tableWrap: "fleet-md-table-wrap",
    table: "fleet-md-table",
    hr: "fleet-md-hr",
    image: "fleet-md-image",
    link: "fleet-link",
  },
  headingLevelOffset: 0,
  allowImages: false,
};

// ─── Inline rendering ────────────────────────────────────────────────────

const INLINE_RE =
  /(!\[[^\]]*\]\([^)\s]+\)|\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*]+\*|_[^_]+_)/g;
const IMAGE_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/;
const LINK_RE = /^\[([^\]]+)\]\(([^)\s]+)\)$/;

function renderInline(text: string, keyPrefix: string, variant: MarkdownLiteVariant): ReactNode[] {
  const { theme } = variant;
  const parts = text.split(INLINE_RE);
  return parts
    .filter((part) => part !== "")
    .map((part, idx) => {
      const key = `${keyPrefix}-${idx}`;
      const image = IMAGE_RE.exec(part);
      if (image) {
        const [, alt, rawSrc] = image;
        // allowImages false (chat) short-circuits BEFORE the sanitizer: a
        // chat turn never renders an <img> at all, safe URL or not.
        const src = variant.allowImages ? safeMarkdownLiteImageSrc(rawSrc) : null;
        if (!src) return <span key={key}>{alt || part}</span>;
        return <img key={key} src={src} alt={alt} loading="lazy" className={theme.image} />;
      }
      if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
        return <strong key={key}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("__") && part.endsWith("__") && part.length >= 4) {
        return <strong key={key}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
        return (
          <code key={key} className={theme.inlineCode}>
            {part.slice(1, -1)}
          </code>
        );
      }
      const link = LINK_RE.exec(part);
      if (link) {
        const [, label, rawHref] = link;
        const href = safeMarkdownLiteHref(rawHref);
        if (!href) return <span key={key}>{label}</span>;
        return (
          <a key={key} href={href} target="_blank" rel="noreferrer" className={theme.link || undefined}>
            {label}
          </a>
        );
      }
      if (part.startsWith("*") && part.endsWith("*") && part.length >= 2) {
        return <em key={key}>{part.slice(1, -1)}</em>;
      }
      if (part.startsWith("_") && part.endsWith("_") && part.length >= 2) {
        return <em key={key}>{part.slice(1, -1)}</em>;
      }
      return <Fragment key={key}>{part}</Fragment>;
    });
}

/** Inline-only rendering (bold/italic/code/links), for a caller that owns
 *  its own block layout — MemoryTab's memory-file preview. Chat's variant,
 *  i.e. no images. */
export function renderMarkdownLiteInline(text: string, keyPrefix: string): ReactNode[] {
  return renderInline(text, keyPrefix, COMPACT_VARIANT);
}

function renderLines(lines: string[], keyPrefix: string, variant: MarkdownLiteVariant): ReactNode[] {
  const out: ReactNode[] = [];
  lines.forEach((line, idx) => {
    if (idx > 0) out.push(<br key={`${keyPrefix}-br-${idx}`} />);
    out.push(...renderInline(line, `${keyPrefix}-${idx}`, variant));
  });
  return out;
}

function alignStyle(align: Align | undefined): CSSProperties | undefined {
  if (!align || align === "left") return undefined;
  return { textAlign: align };
}

function renderList(block: ListBlock, key: string, variant: MarkdownLiteVariant): ReactNode {
  const Tag = block.ordered ? "ol" : "ul";
  return (
    <Tag key={key} className={variant.theme.list}>
      {block.items.map((item, i) => {
        const itemKey = `${key}-${i}`;
        return (
          <li key={itemKey}>
            {renderInline(item.text, itemKey, variant)}
            {item.children?.map((child, ci) => renderList(child, `${itemKey}-c${ci}`, variant))}
          </li>
        );
      })}
    </Tag>
  );
}

function renderBlock(block: MarkdownLiteBlock, index: number, variant: MarkdownLiteVariant): ReactNode {
  const key = `b-${index}`;
  const { theme } = variant;
  switch (block.kind) {
    case "heading": {
      const content = renderInline(block.text, key, variant);
      const level = Math.min(block.level + variant.headingLevelOffset, 6);
      switch (level) {
        case 1:
          return <h1 key={key} className={theme.heading}>{content}</h1>;
        case 2:
          return <h2 key={key} className={theme.heading}>{content}</h2>;
        case 3:
          return <h3 key={key} className={theme.heading}>{content}</h3>;
        case 4:
          return <h4 key={key} className={theme.heading}>{content}</h4>;
        case 5:
          return <h5 key={key} className={theme.heading}>{content}</h5>;
        default:
          return <h6 key={key} className={theme.heading}>{content}</h6>;
      }
    }
    case "paragraph":
      return (
        <p key={key} className={theme.paragraph}>
          {renderLines(block.lines, key, variant)}
        </p>
      );
    case "code":
      // The language tag is PRESERVED rather than interpreted: `data-language`
      // for CSS/tests, plus the conventional `language-<lang>` class on the
      // <code> so a highlighter could be dropped in later without changing
      // this renderer or the markup it emits. There is no syntax highlighter
      // here, and adding one is not this file's job.
      return (
        <pre key={key} className={theme.codeBlock} data-language={block.lang || undefined}>
          <code className={block.lang ? `language-${block.lang}` : undefined}>{block.code}</code>
        </pre>
      );
    case "quote":
      return (
        <blockquote key={key} className={theme.quote}>
          {renderLines(block.lines, key, variant)}
        </blockquote>
      );
    case "list":
      return renderList(block, key, variant);
    case "table":
      return (
        <div key={key} className={theme.tableWrap}>
          <table className={theme.table}>
            <thead>
              <tr>
                {block.header.map((cell, ci) => (
                  <th key={`${key}-h-${ci}`} style={alignStyle(block.align[ci])}>
                    {renderInline(cell, `${key}-h-${ci}`, variant)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, ri) => (
                <tr key={`${key}-r-${ri}`}>
                  {row.map((cell, ci) => (
                    <td key={`${key}-r-${ri}-${ci}`} style={alignStyle(block.align[ci])}>
                      {renderInline(cell, `${key}-r-${ri}-${ci}`, variant)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "hr":
      return <hr key={key} className={theme.hr} />;
    default:
      return null;
  }
}

function renderMarkdown(text: string, variant: MarkdownLiteVariant): ReactNode {
  const blocks = parseMarkdownLiteBlocks(text);
  if (blocks.length === 0) return null;
  return <>{blocks.map((block, i) => renderBlock(block, i, variant))}</>;
}

/** Project documents (DocumentDetailView / DocumentHistory): images on,
 *  headings demoted one level under the page's own document title. */
export function MarkdownLite({ text }: { text: string }): ReactNode {
  return renderMarkdown(text, DOCUMENT_VARIANT);
}

/** Chat turns, task descriptions and conversation transcripts: NO images
 *  (see the file header — a turn can carry a stranger's text), literal
 *  heading levels, the compact `fleet-md-*` class family. Renders nothing
 *  for empty text — callers needing a non-empty fallback (e.g. a
 *  placeholder for a still-streaming turn) handle that themselves. */
export function MarkdownLiteText({ text }: { text: string }): ReactNode {
  return renderMarkdown(text, COMPACT_VARIANT);
}

// Exported for lib/workspace/markdown-lite.test.ts only — this repo has no
// jsdom/RTL (see that file's header), so the parser and the URL sanitizers
// are what a unit test can drive directly.
export const __testing = {
  parseBlocks: parseMarkdownLiteBlocks,
  safeDocumentHref: safeMarkdownLiteHref,
  safeDocumentImageSrc: safeMarkdownLiteImageSrc,
};
