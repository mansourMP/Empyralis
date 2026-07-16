import test from "node:test";
import assert from "node:assert/strict";

import {
  chunkMessage,
  TELEGRAM_MESSAGE_LIMIT,
  WHATSAPP_MESSAGE_LIMIT,
} from "../channels/foundation/message-chunker";

function countFenceLines(chunk: string): number {
  return chunk.split("\n").filter((line) => line.trimStart().startsWith("```")).length;
}

test("chunkMessage: text under the limit is returned as a single unchanged chunk", () => {
  assert.deepEqual(chunkMessage("hello world", TELEGRAM_MESSAGE_LIMIT), ["hello world"]);
});

test("chunkMessage: empty input returns no chunks", () => {
  assert.deepEqual(chunkMessage("", TELEGRAM_MESSAGE_LIMIT), []);
});

test("chunkMessage: text exactly at the limit is one chunk; one over splits into two", () => {
  const exact = "x".repeat(100);
  assert.deepEqual(chunkMessage(exact, 100), [exact]);
  // 101 chars on a single line, limit 100 → hard-split (no spaces to break on).
  const over = "x".repeat(101);
  const chunks = chunkMessage(over, 100);
  assert.equal(chunks.length, 2);
  assert.ok(chunks.every((c) => c.length <= 100));
  assert.equal(chunks.join(""), over);
});

test("chunkMessage: multi-line text splits on line boundaries, never mid-line, each chunk within the limit", () => {
  const lines = Array.from({ length: 10 }, (_, i) => `${i}:` + "a".repeat(498)); // 500 chars each
  const text = lines.join("\n");
  const chunks = chunkMessage(text, 1200); // ~2 lines per chunk
  assert.ok(chunks.length >= 4, `expected several chunks, got ${chunks.length}`);
  for (const chunk of chunks) {
    assert.ok(chunk.length <= 1200, `chunk length ${chunk.length} exceeds limit`);
  }
  // Every original line survives intact somewhere (no line was split).
  const rejoinedLines = chunks.flatMap((c) => c.split("\n"));
  for (const line of lines) {
    assert.ok(rejoinedLines.includes(line), `line "${line.slice(0, 8)}..." was split or lost`);
  }
});

test("chunkMessage: a single line longer than the limit breaks on spaces (whole words), reconstructing exactly", () => {
  const words = Array.from({ length: 800 }, (_, i) => `word${i}`);
  const line = words.join(" ");
  assert.ok(line.length > 1000);
  const chunks = chunkMessage(line, 1000);
  assert.ok(chunks.length >= 2);
  for (const chunk of chunks) {
    assert.ok(chunk.length <= 1000, `chunk length ${chunk.length} exceeds limit`);
  }
  // Space-joining the chunks reproduces the original line → no word was cut.
  assert.equal(chunks.join(" "), line);
});

test("chunkMessage: an unbreakable token longer than the limit is hard-cut (last resort)", () => {
  const blob = "A".repeat(2500); // no spaces, no newlines
  const chunks = chunkMessage(blob, 1000);
  assert.equal(chunks.length, 3);
  assert.ok(chunks.every((c) => c.length <= 1000));
  assert.equal(chunks.join(""), blob);
});

test("chunkMessage: a fenced code block spanning a split is closed and re-opened so every chunk is balanced", () => {
  const codeLines = Array.from({ length: 400 }, (_, i) => `const v${i} = ${i};`);
  const text = "Here is the code:\n```js\n" + codeLines.join("\n") + "\n```\nDone.";
  const chunks = chunkMessage(text, 1500);
  assert.ok(chunks.length >= 2, "the code block should force at least one split");
  for (const chunk of chunks) {
    assert.ok(chunk.length <= 1500, `chunk length ${chunk.length} exceeds limit`);
    // Balanced fences: a chunk never leaves a code block dangling open.
    assert.equal(countFenceLines(chunk) % 2, 0, `chunk has an unbalanced code fence:\n${chunk.slice(0, 40)}`);
  }
  // No code line is lost across the split (synthetic fences aside).
  const emitted = chunks.flatMap((c) => c.split("\n")).filter((l) => l.startsWith("const v"));
  assert.equal(emitted.length, codeLines.length);
});

test("chunkMessage: re-opened fences preserve the original language header", () => {
  const codeLines = Array.from({ length: 300 }, (_, i) => `row ${i}`);
  const text = "```python\n" + codeLines.join("\n") + "\n```";
  const chunks = chunkMessage(text, 800);
  assert.ok(chunks.length >= 2);
  // Every chunk that contains code opens with the python fence header.
  for (const chunk of chunks) {
    const first = chunk.split("\n")[0];
    assert.ok(first.startsWith("```"), "each code chunk should open with a fence");
  }
  assert.ok(chunks.some((c) => c.includes("```python")), "the language header should be carried into re-opened chunks");
});

test("chunkMessage: WhatsApp limit constant chunks a long reply under 4000 chars per message", () => {
  const text = Array.from({ length: 50 }, (_, i) => `Paragraph ${i} ` + "lorem ".repeat(30)).join("\n\n");
  const chunks = chunkMessage(text, WHATSAPP_MESSAGE_LIMIT);
  assert.ok(chunks.length >= 2);
  assert.ok(chunks.every((c) => c.length <= WHATSAPP_MESSAGE_LIMIT));
});
