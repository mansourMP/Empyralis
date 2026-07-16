import test from "node:test";
import assert from "node:assert/strict";

import {
  renderMarkdownToTelegramHtml,
  renderMarkdownToWhatsApp,
} from "../channels/foundation/channel-markdown";

// ===========================================================================
// Telegram (markdown → HTML for parse_mode)
// ===========================================================================

test("telegram: plain text with no markdown is returned unchanged, with no parse mode", () => {
  const result = renderMarkdownToTelegramHtml("hello world");
  assert.equal(result.formatted, false);
  assert.equal(result.text, "hello world");
  assert.equal(result.parseMode, undefined);
});

test("telegram: text with HTML-special chars but no markdown is NOT escaped (sent as plain)", () => {
  // No parse mode means Telegram shows the literal characters; escaping would
  // be wrong here. Escaping only happens once we commit to HTML formatting.
  const result = renderMarkdownToTelegramHtml("a < b & c > d");
  assert.equal(result.formatted, false);
  assert.equal(result.text, "a < b & c > d");
});

test("telegram: bold, italic, strikethrough render to HTML tags with parse mode html", () => {
  assert.deepEqual(renderMarkdownToTelegramHtml("**hi**"), { text: "<b>hi</b>", parseMode: "html", formatted: true });
  assert.deepEqual(renderMarkdownToTelegramHtml("*hi*"), { text: "<i>hi</i>", parseMode: "html", formatted: true });
  assert.deepEqual(renderMarkdownToTelegramHtml("_hi_"), { text: "<i>hi</i>", parseMode: "html", formatted: true });
  assert.deepEqual(renderMarkdownToTelegramHtml("~~hi~~"), { text: "<s>hi</s>", parseMode: "html", formatted: true });
});

test("telegram: inline code and fenced code become <code>/<pre>, contents HTML-escaped", () => {
  assert.equal(renderMarkdownToTelegramHtml("`x < y`").text, "<code>x &lt; y</code>");
  const fenced = renderMarkdownToTelegramHtml("```\na < b\n```");
  assert.ok(fenced.formatted);
  assert.equal(fenced.parseMode, "html");
  assert.ok(fenced.text.includes("<pre>"), `expected <pre>, got ${fenced.text}`);
  assert.ok(fenced.text.includes("a &lt; b"), "code content must be HTML-escaped");
});

test("telegram: markdown INSIDE code is not re-interpreted", () => {
  // The ** inside the code span must stay literal, not become <b>.
  assert.equal(renderMarkdownToTelegramHtml("`**not bold**`").text, "<code>**not bold**</code>");
});

test("telegram: emphasis text is HTML-escaped around the tags", () => {
  assert.equal(renderMarkdownToTelegramHtml("**a & b**").text, "<b>a &amp; b</b>");
});

test("telegram: a markdown link becomes an anchor tag", () => {
  assert.equal(
    renderMarkdownToTelegramHtml("[docs](https://example.com/a)").text,
    '<a href="https://example.com/a">docs</a>',
  );
});

test("telegram: intraword underscores are NOT treated as italics (regression against false positives)", () => {
  const result = renderMarkdownToTelegramHtml("call some_function_name(x)");
  assert.equal(result.formatted, false);
  assert.equal(result.text, "call some_function_name(x)");
});

test("telegram: a lone/odd asterisk with spaces around it is left alone (not italic)", () => {
  const result = renderMarkdownToTelegramHtml("5 * 3 = 15");
  assert.equal(result.formatted, false);
  assert.equal(result.text, "5 * 3 = 15");
});

// ===========================================================================
// WhatsApp (markdown → native inline markers)
// ===========================================================================

test("whatsapp: plain text is returned unchanged", () => {
  assert.equal(renderMarkdownToWhatsApp("hello world"), "hello world");
});

test("whatsapp: bold **x** → *x*, italic *x*/_x_ → _x_, strike ~~x~~ → ~x~", () => {
  assert.equal(renderMarkdownToWhatsApp("**bold**"), "*bold*");
  assert.equal(renderMarkdownToWhatsApp("*italic*"), "_italic_");
  assert.equal(renderMarkdownToWhatsApp("_italic_"), "_italic_");
  assert.equal(renderMarkdownToWhatsApp("~~gone~~"), "~gone~");
});

test("whatsapp: inline code and fenced code use triple-backtick monospace", () => {
  assert.equal(renderMarkdownToWhatsApp("`code`"), "```code```");
  assert.equal(renderMarkdownToWhatsApp("```\nline1\nline2\n```"), "```\nline1\nline2\n```");
});

test("whatsapp: a markdown link degrades to 'label (url)'", () => {
  assert.equal(renderMarkdownToWhatsApp("[docs](https://example.com)"), "docs (https://example.com)");
});

test("whatsapp: markdown inside code is not re-interpreted", () => {
  assert.equal(renderMarkdownToWhatsApp("`**x**`"), "```**x**```");
});

test("whatsapp: intraword underscores are left alone", () => {
  assert.equal(renderMarkdownToWhatsApp("some_function_name"), "some_function_name");
});

test("whatsapp: a mixed sentence converts each construct in place", () => {
  assert.equal(
    renderMarkdownToWhatsApp("Use **bold** and *italic* here"),
    "Use *bold* and _italic_ here",
  );
});
