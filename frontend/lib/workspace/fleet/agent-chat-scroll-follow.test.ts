/**
 * agent-chat-scroll-follow — the "you never lose your place" contract for
 * AgentChat's message list. See the module's own header for the production
 * measurement that motivated it.
 *
 * Run: npx tsx lib/workspace/fleet/agent-chat-scroll-follow.test.ts
 */

import { isNearBottom, shouldSnapChatToBottom, NEAR_BOTTOM_TOLERANCE_PX } from "./agent-chat-scroll-follow";

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

// ── isNearBottom ────────────────────────────────────────────────────────

assert(
  isNearBottom({ scrollTop: 0, scrollHeight: 700, clientHeight: 700 }) === true,
  "content that fits the viewport is always 'at the bottom'",
);
assert(
  isNearBottom({ scrollTop: 12300, scrollHeight: 13000, clientHeight: 700 }) === true,
  "scrolled exactly to the bottom is near the bottom",
);
assert(
  isNearBottom({ scrollTop: 12300 - NEAR_BOTTOM_TOLERANCE_PX, scrollHeight: 13000, clientHeight: 700 }) === true,
  "within tolerance of the bottom still counts as near it",
);
assert(
  isNearBottom({ scrollTop: 12300 - NEAR_BOTTOM_TOLERANCE_PX - 1, scrollHeight: 13000, clientHeight: 700 }) === false,
  "one pixel past tolerance is genuinely scrolled away",
);
assert(
  isNearBottom({ scrollTop: 0, scrollHeight: 13000, clientHeight: 700 }) === false,
  "landed at the very top of a long conversation is not near the bottom — this is the production bug's own reading",
);
assert(
  isNearBottom({ scrollTop: 20369.8 - 92.7 - NEAR_BOTTOM_TOLERANCE_PX + 1, scrollHeight: 20369.8, clientHeight: 92.7 }) === true,
  "real fractional-pixel measurements (sub-pixel DPR rounding) resolve the same as integers",
);

// ── shouldSnapChatToBottom ──────────────────────────────────────────────

assert(
  shouldSnapChatToBottom({ wasFollowing: true, paneHidden: false }) === true,
  "was following, pane visible: new content snaps to bottom",
);
assert(
  shouldSnapChatToBottom({ wasFollowing: false, paneHidden: false }) === false,
  "scrolled up to read history: new content must not yank the reader back down",
);
assert(
  shouldSnapChatToBottom({ wasFollowing: true, paneHidden: true }) === false,
  "a display:none pane has zero-height metrics — never snap from a hidden read, or the catch-up on re-show gets recorded as 'top'",
);
assert(
  shouldSnapChatToBottom({ wasFollowing: false, paneHidden: true }) === false,
  "hidden and already scrolled away: still no snap — nothing about being hidden should ever start following",
);

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}
