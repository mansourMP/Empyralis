/**
 * markdown-lite unit tests — both variants of the app's ONE markdown
 * renderer (see markdown-lite.tsx's header for the variant table and the
 * threat model).
 *
 * Two halves:
 *   • MarkdownLite (documents) — tables, images, nested lists, and the
 *     URL-safety refusals that make links/images safe to point at
 *     document content.
 *   • MarkdownLiteText (chat turns, task descriptions, conversation
 *     transcripts) — the half that regressed: chat used to import a
 *     separate, weaker renderer whose whole block loop was "list, else
 *     paragraph", so an agent's `## Report`, `| Name | Value |` and
 *     ```python block rendered as literal characters. These assert the
 *     real elements, plus that a chat turn NEVER renders an <img> (a turn
 *     can carry an external stranger's text; an <img> is a zero-click
 *     request to a host they chose).
 *
 * This repo has no jsdom/RTL (see package.json — no headless-DOM test
 * harness anywhere), so this cannot mount a component and query the DOM.
 * Two things it CAN do without one, and does:
 *
 *   1. Call `__testing.parseBlocks` directly and assert on the parsed
 *      block tree — the real parser, not a re-implementation of it.
 *   2. Call `MarkdownLite({ text })` / `MarkdownLiteText({ text })` as
 *      plain functions (neither has hooks, so this is safe outside a React
 *      tree) and walk the REAL React element tree returned — plain objects
 *      with `.type`/`.props`, no DOM required — asserting that a dangerous
 *      `[text](javascript:...)` or `![x](data:...)` never produces an
 *      `<a href>`/`<img src>` at all, and that a safe one does with the
 *      exact attribute value.
 *
 * Run: npx tsx lib/workspace/markdown-lite.test.ts
 */

import type { ReactElement, ReactNode } from "react";

import { MarkdownLite, MarkdownLiteText, __testing } from "./markdown-lite";

const { parseBlocks, safeDocumentHref, safeDocumentImageSrc } = __testing;

let passed = 0;
let failed = 0;

function assert(condition: boolean, label: string): void {
  if (condition) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label}`);
  }
}

function assertEqual<T>(actual: T, expected: T, label: string): void {
  if (JSON.stringify(actual) === JSON.stringify(expected)) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL: ${label} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ─── React-element tree walker (no DOM needed) ────────────────────────────

function isElement(node: unknown): node is ReactElement {
  return typeof node === "object" && node !== null && "type" in (node as Record<string, unknown>) && "props" in (node as Record<string, unknown>);
}

function walk(node: ReactNode, visit: (el: ReactElement) => void): void {
  if (Array.isArray(node)) {
    node.forEach((child) => walk(child, visit));
    return;
  }
  if (!isElement(node)) return;
  visit(node);
  const children = (node.props as { children?: ReactNode }).children;
  if (children !== undefined) walk(children, visit);
}

function findAll(root: ReactNode, tagName: string): ReactElement[] {
  const found: ReactElement[] = [];
  walk(root, (el) => {
    if (el.type === tagName) found.push(el);
  });
  return found;
}

function render(text: string): ReactNode {
  return MarkdownLite({ text });
}

/** Reads one prop off a possibly-absent element. A missing element must
 *  FAIL the assertion, never crash the run — otherwise the first regression
 *  hides every assertion after it. */
function propOf(el: ReactElement | undefined, name: string): unknown {
  if (!el) return undefined;
  return (el.props as Record<string, unknown>)[name];
}

// ─── Tables ────────────────────────────────────────────────────────────────

{
  const blocks = parseBlocks("| Name | Role |\n| --- | --- |\n| Ada | Engineer |\n| Grace | Admiral |");
  assertEqual(blocks.length, 1, "table: parses as a single block");
  assertEqual(blocks[0].kind, "table", "table: block kind is table");
  if (blocks[0].kind === "table") {
    assertEqual(blocks[0].header, ["Name", "Role"], "table: header cells parsed");
    assertEqual(blocks[0].align, [null, null], "table: no-colon alignment row is [null, null]");
    assertEqual(blocks[0].rows, [["Ada", "Engineer"], ["Grace", "Admiral"]], "table: body rows parsed");
  }
}

{
  const blocks = parseBlocks("| A | B | C |\n| :-- | :-: | --: |\n| 1 | 2 | 3 |");
  assert(blocks[0].kind === "table", "table: alignment-row variant still parses as table");
  if (blocks[0].kind === "table") {
    assertEqual(blocks[0].align, ["left", "center", "right"], "table: :--/:-:/--: map to left/center/right");
  }
}

{
  const tree = render("| Name | Role |\n| --- | --- |\n| Ada | Engineer |");
  const tables = findAll(tree, "table");
  const theads = findAll(tree, "thead");
  const tbodys = findAll(tree, "tbody");
  const ths = findAll(tree, "th");
  const tds = findAll(tree, "td");
  assertEqual(tables.length, 1, "table: renders a real <table> element");
  assertEqual(theads.length, 1, "table: renders a real <thead>");
  assertEqual(tbodys.length, 1, "table: renders a real <tbody>");
  assertEqual(ths.length, 2, "table: header cells render as <th>, not <td> or <div>");
  assertEqual(tds.length, 2, "table: body cells render as <td>");
  const wraps = findAll(tree, "div").filter((el) => (el.props as { className?: string }).className === "fleet-doc-table-wrap");
  assertEqual(wraps.length, 1, "table: wrapped in its own scroll container (page must never scroll horizontally)");
}

{
  // A pipe inside prose with no valid separator row underneath must NOT be
  // mistaken for a table — it stays a paragraph.
  const blocks = parseBlocks("Cost is $5 | $10 depending on plan.");
  assertEqual(blocks[0].kind, "paragraph", "table: a lone pipe with no alignment row is not a table");
}

// ─── Images ─────────────────────────────────────────────────────────────

{
  const tree = render("![A diagram](https://example.com/diagram.png)");
  const imgs = findAll(tree, "img");
  assertEqual(imgs.length, 1, "image: safe https image renders as <img>");
  assertEqual((imgs[0].props as { src?: string }).src, "https://example.com/diagram.png", "image: src preserved verbatim for a safe URL");
  assertEqual((imgs[0].props as { alt?: string }).alt, "A diagram", "image: alt text preserved");
}

{
  const tree = render("![workspace file](/attachments/photo.png)");
  const imgs = findAll(tree, "img");
  assertEqual(imgs.length, 1, "image: same-origin relative path renders");
  assertEqual((imgs[0].props as { src?: string }).src, "/attachments/photo.png", "image: relative src preserved");
}

// ─── Nested lists ───────────────────────────────────────────────────────

{
  const blocks = parseBlocks("- A\n  - A1\n  - A2\n- B");
  assertEqual(blocks.length, 1, "nested list: parses as one top-level list block");
  const top = blocks[0];
  assert(top.kind === "list", "nested list: top-level block is a list");
  if (top.kind === "list") {
    assertEqual(top.items.length, 2, "nested list: two top-level items (A, B)");
    assertEqual(top.items[0].text, "A", "nested list: first item text");
    const nested = top.items[0].children;
    assert(!!nested && nested.length === 1 && nested[0].kind === "list", "nested list: A carries a nested sub-list");
    if (nested && nested[0].kind === "list") {
      assertEqual(nested[0].items.map((it) => it.text), ["A1", "A2"], "nested list: sub-list items parsed in order");
    }
    assertEqual(top.items[1].children, undefined, "nested list: B has no children");
  }
}

{
  // ordered, 3 levels deep
  const md = "1. One\n   1. One-one\n      1. One-one-one\n   2. One-two\n2. Two";
  const blocks = parseBlocks(md);
  const top = blocks[0];
  assert(top.kind === "list" && top.ordered, "nested list: ordered top-level list");
  if (top.kind === "list") {
    const lvl2 = top.items[0].children?.[0];
    assert(!!lvl2 && lvl2.kind === "list", "nested list: level-2 exists under One");
    if (lvl2 && lvl2.kind === "list") {
      assertEqual(lvl2.items.map((it) => it.text), ["One-one", "One-two"], "nested list: level-2 items in order");
      const lvl3 = lvl2.items[0].children?.[0];
      assert(!!lvl3 && lvl3.kind === "list" && lvl3.items[0].text === "One-one-one", "nested list: level-3 (3 deep) parses");
    }
  }
}

{
  const tree = render("- A\n  - A1\n- B");
  const outerLists = findAll(tree, "ul");
  assertEqual(outerLists.length, 2, "nested list: renders as a real nested <ul> inside the parent <li>, not a flattened list");
}

// ─── URL safety: safeDocumentHref / safeDocumentImageSrc ─────────────────

assertEqual(safeDocumentHref("https://example.com/doc"), "https://example.com/doc", "href: https allowed");
assertEqual(safeDocumentHref("mailto:a@b.com"), "mailto:a@b.com", "href: mailto allowed");
assertEqual(safeDocumentHref("/inside/app"), "/inside/app", "href: same-origin relative path allowed");
assertEqual(safeDocumentHref("#section"), "#section", "href: bare anchor allowed");
assertEqual(safeDocumentHref("javascript:alert(1)"), null, "href: javascript: refused");
assertEqual(safeDocumentHref("data:text/html,<script>alert(1)</script>"), null, "href: data: refused");
assertEqual(safeDocumentHref("vbscript:msgbox(1)"), null, "href: vbscript: refused");
assertEqual(safeDocumentHref("//evil.example.com/x"), null, "href: protocol-relative //host refused (silent host change)");
assertEqual(safeDocumentHref("java\tscript:alert(1)"), null, "href: tab-obfuscated javascript: refused");
assertEqual(safeDocumentHref("JaVaScRiPt:alert(1)"), null, "href: case-varied javascript: refused");

assertEqual(safeDocumentImageSrc("https://example.com/pic.png"), "https://example.com/pic.png", "img src: https allowed");
assertEqual(safeDocumentImageSrc("/attachments/pic.png"), "/attachments/pic.png", "img src: same-origin relative path allowed");
assertEqual(safeDocumentImageSrc("javascript:alert(1)"), null, "img src: javascript: refused");
assertEqual(safeDocumentImageSrc("data:image/png;base64,aaaa"), null, "img src: data: refused");
assertEqual(safeDocumentImageSrc("mailto:a@b.com"), null, "img src: mailto is not an image scheme");
assertEqual(safeDocumentImageSrc("//evil.example.com/x.png"), null, "img src: protocol-relative //host refused");
assertEqual(safeDocumentImageSrc("https://example.com/logo.svg"), null, "img src: .svg refused (a picture that is also a program on this origin)");
assertEqual(safeDocumentImageSrc("/attachments/logo.svg"), null, "img src: relative .svg refused");
assertEqual(safeDocumentImageSrc("https://example.com/logo.svg?v=2"), null, "img src: .svg refused even with a query string after it");
assertEqual(safeDocumentImageSrc("https://example.com/logo.SVG"), null, "img src: .svg refusal is case-insensitive");
assertEqual(safeDocumentImageSrc("https://example.com/notsvg.png"), "https://example.com/notsvg.png", "img src: a .png that merely contains 'sv' is unaffected");

// ─── Security refusals actually reach the rendered tree, not just the
//     sanitizer functions in isolation ───────────────────────────────────

{
  const tree = render("![evil](javascript:alert(document.cookie))");
  assertEqual(findAll(tree, "img").length, 0, "render: javascript: image never becomes a real <img>");
}

{
  const tree = render("![evil](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=)");
  assertEqual(findAll(tree, "img").length, 0, "render: data: image (including an embedded SVG payload) never becomes a real <img>");
}

{
  const tree = render("![logo](https://example.com/logo.svg)");
  assertEqual(findAll(tree, "img").length, 0, "render: .svg image URL never becomes a real <img>");
}

{
  const tree = render("[click me](javascript:alert(document.cookie))");
  assertEqual(findAll(tree, "a").length, 0, "render: javascript: link never becomes a real <a>");
}

{
  const tree = render("[click me](data:text/html,<script>alert(1)</script>)");
  assertEqual(findAll(tree, "a").length, 0, "render: data: link never becomes a real <a>");
}

{
  // Raw HTML passthrough stays unsupported — a literal <script> tag in a
  // document body must render as visible text, never as markup.
  const tree = render("<script>alert(1)</script>");
  assertEqual(findAll(tree, "script").length, 0, "render: a literal <script> in document text never becomes a real element");
}

// ─── Regression: existing constructs still work ──────────────────────────

{
  const tree = render("# Title\n\n**bold** and _italic_ and `code`\n\n> a quote\n\n---\n\n[a link](https://example.com)");
  assertEqual(findAll(tree, "h2").length, 1, "regression: heading still renders (h1 source -> h2, page-title offset)");
  assertEqual(findAll(tree, "strong").length, 1, "regression: bold still renders");
  assertEqual(findAll(tree, "em").length, 1, "regression: italic still renders");
  assertEqual(findAll(tree, "code").length, 1, "regression: inline code still renders");
  assertEqual(findAll(tree, "blockquote").length, 1, "regression: blockquote still renders");
  assertEqual(findAll(tree, "hr").length, 1, "regression: horizontal rule still renders");
  const anchors = findAll(tree, "a");
  assertEqual(anchors.length, 1, "regression: a safe link still renders");
  assertEqual((anchors[0].props as { href?: string }).href, "https://example.com", "regression: safe href preserved verbatim");
}

// ─── Chat variant (MarkdownLiteText) ─────────────────────────────────────
//
// The bug this file's second half exists for: chat imported a separate,
// weaker renderer, so the four constructs below arrived as literal text.
// Each assertion names the ELEMENT, not the parse — a parser that produces
// a heading block whose renderer drops it is the same defect one layer up.

function renderChat(text: string): ReactNode {
  return MarkdownLiteText({ text });
}

/** The exact shape an agent produced in the live thread that surfaced this:
 *  a level-2 heading, a three-item bullet list, a two-column table, a fenced
 *  python block, bold and inline code. */
const CHAT_TURN = [
  "## Report",
  "",
  "- First item",
  "- Second item",
  "- Third item",
  "",
  "| Name | Value |",
  "|------|-------|",
  "| Alpha | 1 |",
  "| Beta | 2 |",
  "",
  "```python",
  'print("hello")',
  "```",
  "",
  "Some **bold** text and some `inline code`.",
].join("\n");

{
  const tree = renderChat(CHAT_TURN);
  // The four that were broken.
  assertEqual(findAll(tree, "h2").length, 1, "chat: '## Report' renders a real <h2>, not the literal text '## Report'");
  assertEqual(findAll(tree, "table").length, 1, "chat: a pipe table renders a real <table>, not literal pipes");
  assertEqual(findAll(tree, "th").length, 2, "chat: table header cells render as <th>");
  assertEqual(findAll(tree, "td").length, 4, "chat: table body cells render as <td>");
  assertEqual(findAll(tree, "pre").length, 1, "chat: a fenced block renders a real <pre>");
  // The two that already worked and must keep working.
  assertEqual(findAll(tree, "ul").length, 1, "chat: bullet list still renders a <ul>");
  assertEqual(findAll(tree, "li").length, 3, "chat: bullet list still renders three <li>");
  assertEqual(findAll(tree, "strong").length, 1, "chat: **bold** still renders <strong>");
}

{
  // Language preserved, not interpreted: no highlighter here, but the tag
  // reaches the DOM so CSS/a future highlighter can read it.
  const tree = renderChat("```python\nprint(\"hello\")\n```");
  const pres = findAll(tree, "pre");
  assertEqual(pres.length, 1, "chat: fenced code renders one <pre>");
  assertEqual(propOf(pres[0], "data-language"), "python", "chat: fence language preserved on the <pre> as data-language");
  const codes = findAll(tree, "code");
  assertEqual(codes.length, 1, "chat: the fenced block's <code> is the only <code> here (not an inline chip)");
  assertEqual(propOf(codes[0], "className"), "language-python", "chat: fence language preserved as the conventional language-<lang> class");
  assertEqual(propOf(codes[0], "children"), 'print("hello")', "chat: code body preserved verbatim, backticks consumed");
}

{
  const tree = renderChat("```\nplain\n```");
  const pres = findAll(tree, "pre");
  assertEqual(propOf(pres[0], "data-language"), undefined, "chat: a fence with no language sets no data-language");
  assertEqual(propOf(findAll(tree, "code")[0], "className"), undefined, "chat: a fence with no language sets no language-* class");
}

{
  const tree = renderChat("See [the docs](https://example.com/guide) for more.");
  const anchors = findAll(tree, "a");
  assertEqual(anchors.length, 1, "chat: a safe link renders a real <a>");
  assertEqual(propOf(anchors[0], "href"), "https://example.com/guide", "chat: safe href preserved verbatim");
  assertEqual(propOf(anchors[0], "rel"), "noreferrer", "chat: external link carries rel=noreferrer (no Referer leak, implies noopener)");
  assertEqual(propOf(anchors[0], "className"), "fleet-link", "chat: link carries the class chat CSS styles");
}

{
  const tree = renderChat("> quoted from the ticket\n> second line");
  assertEqual(findAll(tree, "blockquote").length, 1, "chat: '>' renders a real <blockquote>");
}

{
  const tree = renderChat("1. one\n2. two");
  assertEqual(findAll(tree, "ol").length, 1, "chat: an ordered list renders a real <ol>");
  assertEqual(findAll(tree, "li").length, 2, "chat: ordered list items render");
}

{
  const tree = renderChat("*italic* and _also italic_");
  assertEqual(findAll(tree, "em").length, 2, "chat: italic renders <em>");
}

{
  const tree = renderChat("# One\n\n## Two\n\n### Three");
  assertEqual(findAll(tree, "h1").length, 1, "chat: heading levels are literal — '#' is an h1 (a chat turn is an <article>, its own sectioning root)");
  assertEqual(findAll(tree, "h2").length, 1, "chat: '##' is an h2");
  assertEqual(findAll(tree, "h3").length, 1, "chat: '###' is an h3");
}

{
  // Plain prose must not acquire block markup it never asked for.
  const tree = renderChat("Just a sentence.\nAnd a second line.");
  assertEqual(findAll(tree, "p").length, 1, "chat: two consecutive lines stay one paragraph");
  assertEqual(findAll(tree, "br").length, 1, "chat: a single newline renders a soft <br>, preserving how it was typed");
}

// ─── Chat variant: images are refused, always ────────────────────────────
//
// NOT a feature gap. A chat turn can carry text an external stranger sent
// the agent over a channel; an <img src="https://attacker/..."> is a
// zero-click request from the owner's browser to a host they chose (IP,
// User-Agent, read confirmation), and the app's CSP allows img-src https:
// because DOCUMENTS need it. Documents keep images; chat never gets them.

{
  const tree = renderChat("![a diagram](https://example.com/diagram.png)");
  assertEqual(findAll(tree, "img").length, 0, "chat: even a perfectly safe https image never renders an <img>");
}

{
  const tree = renderChat("![tracker](/attachments/pixel.png)");
  assertEqual(findAll(tree, "img").length, 0, "chat: a same-origin image is refused too — the rule is the surface, not the URL");
}

{
  const tree = renderChat("![evil](javascript:alert(document.cookie))");
  assertEqual(findAll(tree, "img").length, 0, "chat: javascript: image never renders an <img>");
}

{
  const tree = renderChat("[click me](javascript:alert(document.cookie))");
  assertEqual(findAll(tree, "a").length, 0, "chat: javascript: link never becomes a real <a>");
}

{
  const tree = renderChat("[click me](data:text/html,<script>alert(1)</script>)");
  assertEqual(findAll(tree, "a").length, 0, "chat: data: link never becomes a real <a>");
}

{
  const tree = renderChat("[go](//evil.example.com/x)");
  assertEqual(findAll(tree, "a").length, 0, "chat: protocol-relative //host link refused (silent host change)");
}

{
  const tree = renderChat("<script>alert(1)</script>");
  assertEqual(findAll(tree, "script").length, 0, "chat: a literal <script> in a turn never becomes a real element");
}

// ─── The two variants share one implementation ───────────────────────────

{
  // Documents keep images — the axis genuinely differs, and asserting it
  // here keeps "chat has no images" from being read as "images are broken".
  const docTree = MarkdownLite({ text: "![a diagram](https://example.com/diagram.png)" });
  assertEqual(findAll(docTree, "img").length, 1, "variants: a document still renders a safe image");
  const chatTree = renderChat("![a diagram](https://example.com/diagram.png)");
  assertEqual(findAll(chatTree, "img").length, 0, "variants: the same markdown renders no image in a chat turn");
}

{
  // Documents demote by one (their page renders the title above the body);
  // chat does not. Same parser, same block, two renderings.
  const docTree = MarkdownLite({ text: "## Report" });
  assertEqual(findAll(docTree, "h3").length, 1, "variants: a document's '##' is demoted to h3 under its page title");
  const chatTree = renderChat("## Report");
  assertEqual(findAll(chatTree, "h2").length, 1, "variants: a chat turn's '##' stays an h2");
}

// ─── Summary ───────────────────────────────────────────────────────────────

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) {
  process.exit(1);
}
