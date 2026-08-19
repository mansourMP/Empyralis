/**
 * resolveAgentChatViewState — the fix for the "a failed load renders as a
 * calm empty conversation" bug (see the module's own header comment for the
 * live incident). The assertions that matter are the ones proving "error"
 * and "empty" can never collapse into each other, and that existing
 * content survives a background failure instead of being wiped by it.
 *
 * Run: npx tsx lib/workspace/fleet/agent-chat-view-state.test.ts
 */

import { resolveAgentChatViewState } from "./agent-chat-view-state";

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

// ── loading always wins, regardless of any stale error/message state ──────
assert(
  resolveAgentChatViewState({ loading: true, error: null, messageCount: 0, streamingActive: false }) === "loading",
  "loading with nothing else going on is 'loading'",
);
assert(
  resolveAgentChatViewState({ loading: true, error: "boom", messageCount: 3, streamingActive: false }) === "loading",
  "loading wins even with a leftover error and leftover messages from a prior thread",
);

// ── the bug itself: a failed load with nothing to show must say so ────────
assert(
  resolveAgentChatViewState({ loading: false, error: "Could not load the conversation.", messageCount: 0, streamingActive: false })
    === "error",
  "a failed load with zero messages is 'error', never silently 'empty'",
);

// ── a genuinely empty, never-started conversation is still honest ─────────
assert(
  resolveAgentChatViewState({ loading: false, error: null, messageCount: 0, streamingActive: false }) === "empty",
  "no error and no messages is a real empty conversation",
);

// ── existing content survives a failed BACKGROUND refresh ─────────────────
assert(
  resolveAgentChatViewState({ loading: false, error: "Could not load the conversation.", messageCount: 5, streamingActive: false })
    === "content",
  "messages already on screen are not wiped by a later failed refresh",
);
assert(
  resolveAgentChatViewState({ loading: false, error: "Could not load the conversation.", messageCount: 0, streamingActive: true })
    === "content",
  "an actively streaming reply counts as content even if the thread reload that raced it failed",
);

// ── ordinary success ────────────────────────────────────────────────────
assert(
  resolveAgentChatViewState({ loading: false, error: null, messageCount: 4, streamingActive: false }) === "content",
  "messages with no error is ordinary content",
);

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}
