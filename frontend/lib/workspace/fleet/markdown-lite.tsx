"use client";

/**
 * A small, dependency-free markdown renderer for project documents
 * (DocumentDetailView.tsx). There is no markdown package anywhere in this
 * repo's package.json today — adding one for a single read-only render
 * surface was weighed against writing the ~150 lines this file is, and lost:
 * a project document's markdown is technical/structural (headings, lists,
 * code, links, emphasis), not prose that needs full CommonMark fidelity
 * (footnotes, nested blockquotes, tables, raw HTML passthrough). This covers
 * exactly the syntax a person typing into the plain textarea next to it
 * would reach for.
 *
 * SAFE BY CONSTRUCTION: every block below is built as real React elements
 * from parsed text, never `dangerouslySetInnerHTML` — there is no HTML
 * parsing step for a malicious `<script>` or `<img onerror>` in a document
 * body to survive. A literal "<div>" in a document renders as the four
 * visible characters "<div>", not a tag.
 *
 * Supported: # .. ###### headings, **bold** or __bold__, *italic* or
 * _italic_, `inline code`, fenced code blocks (language tag ignored — no syntax
 * highlighter here), [text](url) links, - / * / + unordered lists, 1. 2.
 * ordered lists, > blockquotes, --- horizontal rules, and paragraphs with
 * single newlines rendered as soft breaks (so a document typed without
 * blank-line-per-paragraph discipline still reads the way it was typed).
 * NOT supported: nested lists, tables, footnotes, raw HTML, reference-style
 * links. A document that uses one of those still renders — the unsupported
 * markup just prints as literal text instead of being interpreted.
 */

import { Fragment, type ReactNode } from "react";

type Block =
  | { kind: "heading"; level: 1 | 2 | 3 | 4 | 5 | 6; text: string }
  | { kind: "paragraph"; lines: string[] }
  | { kind: "code"; lang: string; code: string }
  | { kind: "quote"; lines: string[] }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "hr" };

const HR_RE = /^(?:-{3,}|\*{3,}|_{3,})$/;
const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const UL_ITEM_RE = /^[-*+]\s+(.*)$/;
const OL_ITEM_RE = /^\d+\.\s+(.*)$/;
const QUOTE_RE = /^>\s?(.*)$/;
const FENCE_RE = /^```\s*([\w-]*)\s*$/;

function parseBlocks(markdown: string): Block[] {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
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

    const ulMatch = UL_ITEM_RE.exec(line);
    const olMatch = OL_ITEM_RE.exec(line);
    if (ulMatch || olMatch) {
      const ordered = Boolean(olMatch);
      const itemRe = ordered ? OL_ITEM_RE : UL_ITEM_RE;
      const items: string[] = [(ulMatch || olMatch)![1]];
      i += 1;
      while (i < lines.length) {
        const m = itemRe.exec(lines[i]);
        if (!m) break;
        items.push(m[1]);
        i += 1;
      }
      blocks.push({ kind: "list", ordered, items });
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
      !UL_ITEM_RE.test(lines[i]) &&
      !OL_ITEM_RE.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: "paragraph", lines: paraLines });
  }

  return blocks;
}

const INLINE_RE = /(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*]+\*|_[^_]+_)/g;
const LINK_RE = /^\[([^\]]+)\]\(([^)\s]+)\)$/;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(INLINE_RE);
  return parts
    .filter((part) => part !== "")
    .map((part, idx) => {
      const key = `${keyPrefix}-${idx}`;
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
        return (
          <a key={key} href={link[2]} target="_blank" rel="noreferrer">
            {link[1]}
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
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag key={key} className="fleet-doc-list">
          {block.items.map((item, i) => (
            <li key={`${key}-${i}`}>{renderInline(item, `${key}-${i}`)}</li>
          ))}
        </Tag>
      );
    }
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
