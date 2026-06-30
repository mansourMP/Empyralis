import { strict as assert } from "node:assert";
import { PerSessionRateLimiter } from "../src/rate-limiter.js";

const TEST_SESSION = "test-rate-limiter-" + Date.now().toString(36);

async function setup() {
  const rl = new PerSessionRateLimiter(TEST_SESSION);
  await rl.clear();
  await rl.reset(); // start with full bucket (20 tokens)
  return rl;
}

async function teardown(rl) {
  await rl.clear();
  await rl.disconnect();
}

async function test_initialAllow() {
  const rl = await setup();
  try {
    const result = await rl.consume(1);
    assert.equal(result.allowed, true);
    assert.equal(result.tokensLeft, 19);
    console.log("  ✓ initial consume allows, tokens 20→19");
  } finally {
    await teardown(rl);
  }
}

async function test_burst20() {
  const rl = await setup();
  try {
    for (let i = 0; i < 20; i++) {
      const result = await rl.consume(1);
      assert.equal(result.allowed, true, `consume ${i + 1} should be allowed`);
    }
    // 21st should be rejected
    const blocked = await rl.consume(1);
    assert.equal(blocked.allowed, false);
    assert.ok(blocked.retryAfterMs > 0, `retryAfterMs should be > 0, got ${blocked.retryAfterMs}`);
    assert.equal(blocked.tokensLeft, 0);
    console.log("  ✓ burst of 20 allowed, 21st blocked");
  } finally {
    await teardown(rl);
  }
}

async function test_burst30() {
  const rl = await setup();
  try {
    let allowed = 0;
    let blocked = 0;
    let firstBlockRetryMs = 0;

    for (let i = 0; i < 30; i++) {
      const result = await rl.consume(1);
      if (result.allowed) {
        allowed++;
      } else {
        blocked++;
        if (!firstBlockRetryMs) firstBlockRetryMs = result.retryAfterMs;
      }
    }

    assert.equal(allowed, 20, "first 20 should be allowed");
    assert.equal(blocked, 10, "next 10 should be blocked");
    assert.ok(firstBlockRetryMs >= 3000, `retryAfterMs should be >= 3000ms, got ${firstBlockRetryMs}`);
    console.log(`  ✓ burst of 30: ${allowed} allowed, ${blocked} blocked, retryAfterMs=${firstBlockRetryMs}ms`);
  } finally {
    await teardown(rl);
  }
}

async function test_tokenRefillAfterIdle() {
  const rl = await setup();
  try {
    // Drain all 20 tokens
    for (let i = 0; i < 20; i++) {
      await rl.consume(1);
    }
    
    // Blocked
    const blocked = await rl.consume(1);
    assert.equal(blocked.allowed, false);

    // Wait 4 seconds (should refill 1+ tokens)
    console.log("  waiting 4s for token refill...");
    await new Promise(resolve => setTimeout(resolve, 4000));

    // Should be allowed now
    const afterWait = await rl.consume(1);
    assert.equal(afterWait.allowed, true, "should be allowed after 4s idle");
    assert.ok(afterWait.tokensLeft >= 0);
    console.log(`  ✓ refill after 4s idle: allowed, tokensLeft=${afterWait.tokensLeft}`);
  } finally {
    await teardown(rl);
  }
}

async function test_status() {
  const rl = await setup();
  try {
    await rl.consume(3);
    const status = await rl.getStatus();
    assert.equal(status.tokensLeft, 17);
    assert.equal(status.maxTokens, 20);
    console.log("  ✓ getStatus returns correct token count");
  } finally {
    await teardown(rl);
  }
}

async function test_reset() {
  const rl = await setup();
  try {
    // Drain
    for (let i = 0; i < 15; i++) await rl.consume(1);
    
    const before = await rl.getStatus();
    assert.equal(before.tokensLeft, 5);
    
    // Reset
    await rl.reset();
    const after = await rl.getStatus();
    assert.equal(after.tokensLeft, 20);
    console.log("  ✓ reset fills bucket back to 20");
  } finally {
    await teardown(rl);
  }
}

async function test_retryAfterIncreasesForLargerDeficit() {
  const rl = await setup();
  try {
    // Drain to 2 tokens
    for (let i = 0; i < 18; i++) await rl.consume(1);
    
    // Try to consume 5 — deficit of 3
    const result = await rl.consume(5);
    assert.equal(result.allowed, false);
    // Deficit = 5 - 2 = 3, so retryAfterMs should be ~3 * 3000ms
    assert.ok(result.retryAfterMs >= 9000, `retryAfterMs for deficit of 3 should be >= 9000ms, got ${result.retryAfterMs}`);
    console.log(`  ✓ retryAfterMs scales with deficit: ${result.retryAfterMs}ms`);
  } finally {
    await teardown(rl);
  }
}

async function test_dripFeed() {
  const rl = await setup();
  try {
    // Drain all tokens
    for (let i = 0; i < 20; i++) await rl.consume(1);
    
    // Now tokens should drip-feed at ~1 per 3s
    let fed = 0;
    for (let tick = 0; tick < 15; tick++) {  // 15 * ~1s = 15s window
      await new Promise(resolve => setTimeout(resolve, 1000));
      const result = await rl.consume(1);
      if (result.allowed) {
        fed++;
        console.log(`    drip-fed token at +${(tick + 1)}s`);
      }
    }
    
    // After 15s, we should have gotten ~4-5 tokens (15/3 = 5)
    assert.ok(fed >= 3, `should drip-feed at least 3 tokens in ~15s, got ${fed}`);
    assert.ok(fed <= 7, `should not exceed ~7 tokens in ~15s, got ${fed}`);
    console.log(`  ✓ drip-feed: ${fed} tokens in ~15s (expecting ~4-5)`);
  } finally {
    await teardown(rl);
  }
}

// ── Run ────────────────────────────────────────────────────────

const tests = [
  { name: "initial consume", fn: test_initialAllow },
  { name: "burst of 20", fn: test_burst20 },
  { name: "burst of 30 → 20 allowed, 10 blocked", fn: test_burst30 },
  { name: "token refill after idle", fn: test_tokenRefillAfterIdle },
  { name: "getStatus", fn: test_status },
  { name: "reset", fn: test_reset },
  { name: "retryAfterMs scales with deficit", fn: test_retryAfterIncreasesForLargerDeficit },
  { name: "drip-feed after burst", fn: test_dripFeed },
];

let passed = 0;
let failed = 0;
const startTime = Date.now();

for (const { name, fn } of tests) {
  try {
    await fn();
    passed++;
  } catch (err) {
    failed++;
    console.error(`  ✗ ${name}: ${err.message}`);
  }
}

const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
console.log(`\n${passed} passed, ${failed} failed, ${tests.length} total (${elapsed}s)`);
if (failed > 0) process.exit(1);
