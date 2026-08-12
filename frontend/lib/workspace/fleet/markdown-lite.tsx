"use client";

/**
 * A small, dependency-free markdown renderer for project documents
 * (DocumentDetailView.tsx). There is no markdown package anywhere in this
 * repo's package.json today — adding one for a single read-only render
 * surface was weighed against writing the ~350 lines this file is, and
 * lost: a project document's markdown is technical/structural (headings,
 * lists, tables, images, code, links, emphasis), not prose that needs full
 * CommonMark fidelity (footnotes, raw HTML passthrough, reference-style
 * links).
 *
 * SAFE BY CONSTRUCTION: every block below is built as real React elements
 * from parsed text, never `dangerouslySetInnerHTML` — there is no HTML
 * parsing step for a malicious `<script>` or `<img onerror>` in a document
 * body to survive. A literal "<div>" in a document renders as the four
 * visible characters "<div>", not a tag.
 *
 * Supported: # .. ###### headings, **bold** or __bold__, *italic* or
 * _italic_, `inline code`, fenced code blocks (language tag ignored — no
 * syntax highlighter here), [text](url) links, ![alt](url) images,
 * GitHub-flavoured pipe tables (header row, `:--`/`:-:`/`--:` alignment
 * row, body rows — rendered as real <table>/<thead>/<tbody>/<th>/<td>, not
 * divs), - / * / + unordered lists and 1. 2. ordered lists (with
 * indentation-based nesting, any depth), > blockquotes, --- horizontal
 * rules, and paragraphs with single newlines rendered as soft breaks (so a
 * document typed without blank-line-per-paragraph discipline still reads
 * the way it was typed).
 * NOT supported: footnotes, raw HTML, reference-style links. A document
 * that uses one of those still renders — the unsupported markup just
 * prints as literal text instead of being interpreted.
 *
 * LINK/IMAGE URL SAFETY: a `[text](url)` or `![alt](url)` doesn't render
 * `url` verbatim into `href`/`src` — see safeDocumentHref/
 * safeDocumentImageSrc below. Both refuse `javascript:`/`data:`/any scheme
 * outside an explicit allowlist and protocol-relative `//host` links (which
 * silently change host); a refused URL falls back to inert text instead of
 * a live href/src. Images additionally refuse anything that resolves to a
 * `.svg` path — see CLAUDE.md's upload-policy note: attachments are served
 * straight back by FileResponse, so an SVG is a picture that is also a
 * program on the workspace's own origin, and this renderer must not give
 * one a path to execute as an <img>.
 */

import { Fragment, type CSSProperties, type ReactNode } from "react";

type Align = "left" | "center" | "right" | null;

type ListItemNode = { text: string; children?: ListBlock[] };
type ListBlock = { kind: "list"; ordered: boolean; items: ListItemNode[] };

type Block =
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

function parseBlocks(markdown: string): Block[] {
  const lines = String(markdown || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map(expandLeadingTabs);
  const blocks: Block[] = [];
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
// real `<a href>`/`<img src>` — so a document body containing
// `[click me](javascript:...)` or `![x](javascript:...)` must never reach
// that attribute unvalidated, the same class of hole
// `lib/workspace/markdown-lite.tsx` (the separate chat-turn renderer)
// already guards its own links against. Allow only absolute http/https
// (plus mailto for links) and same-origin relative paths/anchors (leading
// `/` — but not the protocol-relative `//host` form, which silently
// changes host — or `#`); anything else (javascript:, data:, vbscript:, a
// bare scheme-less string, ...) returns null so the caller renders the
// visible text as inert plain text instead of a live href/src.

const SAFE_LINK_SCHEMES = new Set(["http:", "https:", "mailto:"]);
const SAFE_IMAGE_SCHEMES = new Set(["http:", "https:"]);
const SCHEME_RE = /^([a-zA-Z][a-zA-Z0-9+.-]*):/;

function cleanUrl(url: string): string {
  // Strip characters browsers ignore when sniffing a scheme (tabs,
  // newlines) -- "java\tscript:" is a classic filter-bypass trick -- then
  // trim surrounding whitespace.
  return url.replace(/[\t\n\r]/g, "").trim();
}

function safeDocumentHref(url: string): string | null {
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

function safeDocumentImageSrc(url: string): string | null {
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

// ─── Inline rendering ────────────────────────────────────────────────────

const INLINE_RE =
  /(!\[[^\]]*\]\([^)\s]+\)|\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*]+\*|_[^_]+_)/g;
const IMAGE_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/;
const LINK_RE = /^\[([^\]]+)\]\(([^)\s]+)\)$/;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(INLINE_RE);
  return parts
    .filter((part) => part !== "")
    .map((part, idx) => {
      const key = `${keyPrefix}-${idx}`;
      const image = IMAGE_RE.exec(part);
      if (image) {
        const [, alt, rawSrc] = image;
        const src = safeDocumentImageSrc(rawSrc);
        if (!src) return <span key={key}>{alt || part}</span>;
        return <img key={key} src={src} alt={alt} loading="lazy" className="fleet-doc-image" />;
      }
      if (part.startsWith("**") && part.endsWith("**") && part.length >= 4) {
        return <strong key={key}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("__") && part.endsWith("__") && part.length >= 4) {
        return <strong key={key}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
        return (
          <code key={key} className="fleet-doc-inline-code">
            {part.slice(1, -1)}
          </code>
        );
      }
      const link = LINK_RE.exec(part);
      if (link) {
        const [, text2, rawHref] = link;
        const href = safeDocumentHref(rawHref);
        if (!href) return <span key={key}>{text2}</span>;
        return (
          <a key={key} href={href} target="_blank" rel="noreferrer">
            {text2}
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

function renderLines(lines: string[], keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  lines.forEach((line, idx) => {
    if (idx > 0) out.push(<br key={`${keyPrefix}-br-${idx}`} />);
    out.push(...renderInline(line, `${keyPrefix}-${idx}`));
  });
  return out;
}

function alignStyle(align: Align | undefined): CSSProperties | undefined {
  if (!align || align === "left") return undefined;
  return { textAlign: align };
}

function renderList(block: ListBlock, key: string): ReactNode {
  const Tag = block.ordered ? "ol" : "ul";
  return (
    <Tag key={key} className="fleet-doc-list">
      {block.items.map((item, i) => {
        const itemKey = `${key}-${i}`;
        return (
          <li key={itemKey}>
            {renderInline(item.text, itemKey)}
            {item.children?.map((child, ci) => renderList(child, `${itemKey}-c${ci}`))}
          </li>
        );
      })}
    </Tag>
  );
}

function renderBlock(block: Block, index: number): ReactNode {
  const key = `b-${index}`;
  switch (block.kind) {
    case "heading": {
      // +1: a document's own #heading is a SECTION inside the page, whose
      // real <h1>/<h2> is the page title above it (same "one real heading
      // per level" discipline page.tsx's own MAN-145 note follows) — a
      // document's "# Title" becomes an h2, "## Sub" an h3, and so on,
      // capped at h6.
      const content = renderInline(block.text, key);
      switch (Math.min(block.level + 1, 6)) {
        case 2:
          return <h2 key={key} className="fleet-doc-heading">{content}</h2>;
        case 3:
          return <h3 key={key} className="fleet-doc-heading">{content}</h3>;
        case 4:
          return <h4 key={key} className="fleet-doc-heading">{content}</h4>;
        case 5:
          return <h5 key={key} className="fleet-doc-heading">{content}</h5>;
        default:
          return <h6 key={key} className="fleet-doc-heading">{content}</h6>;
      }
    }
    case "paragraph":
      return (
        <p key={key} className="fleet-doc-paragraph">
          {renderLines(block.lines, key)}
        </p>
      );
    case "code":
      return (
        <pre key={key} className="fleet-doc-code">
          <code>{block.code}</code>
        </pre>
      );
    case "quote":
      return (
        <blockquote key={key} className="fleet-doc-quote">
          {renderLines(block.lines, key)}
        </blockquote>
      );
    case "list":
      return renderList(block, key);
    case "table":
      return (
        <div key={key} className="fleet-doc-table-wrap">
          <table className="fleet-doc-table">
            <thead>
              <tr>
                {block.header.map((cell, ci) => (
                  <th key={`${key}-h-${ci}`} style={alignStyle(block.align[ci])}>
                    {renderInline(cell, `${key}-h-${ci}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, ri) => (
                <tr key={`${key}-r-${ri}`}>
                  {row.map((cell, ci) => (
                    <td key={`${key}-r-${ri}-${ci}`} style={alignStyle(block.align[ci])}>
                      {renderInline(cell, `${key}-r-${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "hr":
      return <hr key={key} className="fleet-doc-hr" />;
    default:
      return null;
  }
}

export function MarkdownLite({ text }: { text: string }): ReactNode {
  const blocks = parseBlocks(text);
  return <>{blocks.map((block, i) => renderBlock(block, i))}</>;
}

// Exported for lib/workspace/fleet/markdown-lite.test.ts only — this repo
// has no jsdom/RTL (see that file's header), so the parser and the URL
// sanitizers are what a unit test can actually drive directly.
export const __testing = {
  parseBlocks,
  safeDocumentHref,
  safeDocumentImageSrc,
};
