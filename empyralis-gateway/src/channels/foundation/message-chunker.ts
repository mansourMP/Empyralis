/**
 * Shared outbound message chunker for personal channels.
 *
 * When an agent reply exceeds a channel's per-message length limit
 * (Telegram ~4096 chars, WhatsApp practically large but we chunk well under
 * it for readability), splitting it into several sequential messages reads
 * far better — and avoids the hard send error / truncation — than dropping
 * the tail. This module does that split at NATURAL boundaries:
 *
 *   - It packs whole lines greedily into each chunk, so a break never lands
 *     mid-word (a line is only ever sub-split when the single line itself
 *     exceeds the limit, and even then it breaks at a space where possible).
 *   - It tracks fenced ``` code blocks and never leaves one dangling across a
 *     chunk boundary: if a break falls inside an open fence, the current chunk
 *     is closed with a synthetic ``` and the next chunk re-opens the same
 *     fence header, so every emitted chunk is independently well-formed
 *     (important because each chunk is markdown-rendered on its own before
 *     send — see channel-markdown.ts).
 *
 * Pure and channel-agnostic: the caller supplies the limit. Concatenating the
 * returned chunks reproduces the original text's content (modulo the cosmetic
 * fence close/re-open lines the chunker inserts to keep code blocks valid).
 */

/** Telegram's hard per-message ceiling (visible/entity length, UTF-16). */
export const TELEGRAM_MESSAGE_LIMIT = 4096;

/** WhatsApp tolerates far more, but long single bubbles read poorly; chunk
 *  well under the platform max so replies arrive as digestible messages. */
export const WHATSAPP_MESSAGE_LIMIT = 4000;

/** Splits a single over-long line at the last space at/under `limit`, falling
 *  back to a hard cut only when there is no space to break on (a single
 *  unbroken token longer than the limit — a URL, a base64 blob, etc.). */
function splitLongLine(line: string, limit: number): string[] {
  const out: string[] = [];
  let rest = line;
  while (rest.length > limit) {
    let cut = rest.lastIndexOf(" ", limit);
    // No usable space in the window → hard cut at the limit (mid-token is
    // unavoidable here; still never happens for ordinary prose).
    if (cut <= 0) {
      cut = limit;
    }
    out.push(rest.slice(0, cut));
    rest = rest.slice(cut).replace(/^ +/, "");
  }
  if (rest.length > 0) {
    out.push(rest);
  }
  return out;
}

function isFenceLine(line: string): boolean {
  return line.trimStart().startsWith("```");
}

/**
 * Splits `text` into chunks no longer than `limit`, breaking on line
 * boundaries (and, only when a single line is itself too long, on spaces).
 * Returns `[text]` unchanged when it already fits, and `[]` for empty input.
 */
export function chunkMessage(text: string, limit: number): string[] {
  if (typeof text !== "string" || text.length === 0) {
    return [];
  }
  if (text.length <= limit) {
    return [text];
  }
  // Guard against a nonsensical limit forcing an infinite loop / empty chunks.
  const safeLimit = Math.max(1, Math.floor(limit));

  const chunks: string[] = [];
  let current: string[] = [];
  let currentLen = 0;
  // Whether `current` currently sits inside an (unclosed) fenced code block,
  // and the exact opening fence line to re-open with in the next chunk.
  let fenceOpen = false;
  let fenceHeader = "```";

  const flush = (): void => {
    if (current.length === 0) {
      return;
    }
    chunks.push(current.join("\n"));
    current = [];
    currentLen = 0;
  };

  const rawLines = text.split("\n");
  for (const rawLine of rawLines) {
    const pieces = rawLine.length > safeLimit ? splitLongLine(rawLine, safeLimit) : [rawLine];
    for (const line of pieces) {
      const joinCost = current.length > 0 ? 1 : 0; // the "\n" join separator
      // Reserve room for a synthetic closing fence ("\n```") if we'd have to
      // break while a code block is open, so the closed chunk still fits.
      const closeReserve = fenceOpen ? 4 : 0;
      if (current.length > 0 && currentLen + joinCost + line.length + closeReserve > safeLimit) {
        if (fenceOpen) {
          current.push("```");
        }
        flush();
        if (fenceOpen) {
          current.push(fenceHeader);
          currentLen = fenceHeader.length;
        }
      }
      const sep = current.length > 0 ? 1 : 0;
      current.push(line);
      currentLen += sep + line.length;
      if (isFenceLine(line)) {
        if (!fenceOpen) {
          fenceOpen = true;
          fenceHeader = line;
        } else {
          fenceOpen = false;
          fenceHeader = "```";
        }
      }
    }
  }
  flush();
  return chunks.filter((chunk) => chunk.length > 0);
}
