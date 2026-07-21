import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { startSignalCliBridge } from "../bridges/signal-cli-bridge";

// ---------------------------------------------------------------------------
// ADVERSARIAL GAP TEST -- NOW UPDATED TO ASSERT THE FIX (reliability wave
// verification, see docs/design/reliability-audit-2-channels.md and the
// "does it actually heal" audit this file was written for).
//
// signal-cli-sse-reconnect.test.ts already proves the SSE reconnect loop
// recovers from a TRANSIENT drop and that reconnect_attempts climbs (not
// stuck at 0) during an outage. This file asks the next adversarial
// question the existing suite never asks: what happens once
// DEFAULT_RECONNECT_POLICY.maxAttempts (12, foundation/reconnect-utils.ts)
// is actually exhausted?
//
// FIXED: runSignalCliEventLoop (bridges/signal-cli-bridge.ts) no longer
// gives up permanently once reconnectAttempts >= maxAttempts. Once that
// fast exponential-backoff cap is reached, it now keeps looping forever at
// a bounded, much longer steady interval (SSE_LONG_RETRY_INTERVAL_MS, 5
// minutes) instead of returning -- the same never-give-up-just-cap-the-
// delay philosophy as the cloud ws-client's own reconnect loop (cloud/
// reconnect.ts's ReconnectBackoff has no maxAttempts at all, only a capped
// delay). reconnect_attempts stays pinned at maxAttempts while in this
// steady-retry mode (never incremented further), and
// connectSignalCliEvents' onOpen callback resets it to 0 -- and flips
// sse_connected back to true -- the moment signal-cli actually recovers.
// So once signal-cli has been down long enough to exhaust 12 fast attempts
// (~3.5-4 minutes of real backoff), inbound Signal now self-heals on its
// own within SSE_LONG_RETRY_INTERVAL_MS of signal-cli coming back, with no
// operator restart required. /health continues to report the state
// honestly throughout (sse_connected, reconnect_attempts, sse_last_error).
//
// To make 12 real attempts of exponential backoff (which cumulatively sum
// to ~3.5-4 minutes) plus the subsequent long-interval retry fit in a fast
// test, this file clamps the global setTimeout delay for the duration of
// the reconnect loop -- the bridge's own maxAttempts/backoff/long-interval
// constants are not injectable from a test (by design: they're bare
// module-level constants, not passed through SignalCliBridgeOptions), so
// this is the only test-only lever available without touching production
// source.
// ---------------------------------------------------------------------------

async function eventually(assertion: () => void | Promise<void>, timeoutMs = 5_000): Promise<void> {
  const startedAt = Date.now();
  let lastError: unknown;
  while (Date.now() - startedAt < timeoutMs) {
    try {
      await assertion();
      return;
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  }
  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error("Timed out waiting for assertion.");
}

async function readHealth(bridgeUrl: string): Promise<Record<string, unknown>> {
  const response = await fetch(`${bridgeUrl}/health`);
  return response.json() as Promise<Record<string, unknown>>;
}

test(
  "FIXED: after DEFAULT_RECONNECT_POLICY.maxAttempts (12) is exhausted, the Signal SSE loop keeps retrying at a " +
    "bounded long steady interval and self-heals -- sse_connected flips back to true once signal-cli recovers, " +
    "with no operator restart required",
  async () => {
    // Rebuild the fake daemon inline (the helper above hands back a bound
    // method set without an actual listening server yet).
    let acceptEvents = false;
    const eventClients = new Set<http.ServerResponse>();
    const server = http.createServer((request, response) => {
      const url = new URL(request.url || "/", "http://127.0.0.1");
      if (request.method === "GET" && url.pathname === "/api/v1/check") {
        response.writeHead(200, { "content-type": "text/plain" });
        response.end("OK");
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/v1/events") {
        if (!acceptEvents) {
          response.writeHead(500, { "content-type": "application/json" });
          response.end(JSON.stringify({ error: "signal-cli is down" }));
          return;
        }
        response.writeHead(200, {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          connection: "keep-alive",
        });
        response.flushHeaders();
        eventClients.add(response);
        request.on("close", () => eventClients.delete(response));
        return;
      }
      response.writeHead(404, { "content-type": "application/json" });
      response.end(JSON.stringify({ error: "not_found" }));
    });
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const address = server.address();
    const port = typeof address === "object" && address ? address.port : 0;
    const daemonUrl = `http://127.0.0.1:${port}`;

    const originalConsoleError = console.error;
    console.error = () => undefined;
    // Clamp every backoff delay in this process to a few ms so 12 real
    // reconnect attempts finish in well under a second instead of ~4
    // minutes. Only the delay is touched -- callback identity/args and
    // ordinary short internal delays are passed through unchanged.
    const originalSetTimeout = global.setTimeout;
    (global as unknown as { setTimeout: typeof setTimeout }).setTimeout = ((
      fn: (...args: unknown[]) => void,
      ms?: number,
      ...args: unknown[]
    ) => originalSetTimeout(fn, typeof ms === "number" && ms > 20 ? 2 : ms, ...args)) as typeof setTimeout;

    const bridge = await startSignalCliBridge({ signalCliRpcUrl: daemonUrl, account: "+15551234567" });
    try {
      // Exhaustion: reconnect_attempts must reach exactly maxAttempts (12)
      // and then STOP climbing -- proving the loop actually exited rather
      // than merely slowing down.
      await eventually(async () => {
        const health = await readHealth(bridge.url);
        assert.equal(health.sse_connected, false);
        assert.equal(health.reconnect_attempts, 12);
      }, 5_000);

      const attemptsAtExhaustion = (await readHealth(bridge.url)).reconnect_attempts;
      await new Promise((resolve) => originalSetTimeout(resolve, 150));
      const attemptsAfterWaiting = (await readHealth(bridge.url)).reconnect_attempts;
      assert.equal(
        attemptsAfterWaiting,
        attemptsAtExhaustion,
        "reconnect_attempts must stay pinned at 12 in steady-retry mode (never incremented further), not keep " +
          "climbing forever (that would suggest the fast-backoff branch is still being taken)",
      );

      // signal-cli "recovers" -- a real daemon would now accept /api/v1/events
      // again. This is exactly the condition the SSE reconnect loop exists
      // to detect and recover from. Deliberately keep the global setTimeout
      // clamp active here rather than restoring real timers first: the fix
      // means the loop is STILL actively retrying (at SSE_LONG_RETRY_
      // INTERVAL_MS, a bare, non-injectable module constant) rather than
      // having exited, so restoring real timers first would race an
      // already-scheduled clamped-vs-real wait and could make this test
      // wait up to that real multi-minute interval. Keeping the clamp on
      // throughout instead makes every subsequent retry fire in ~2ms,
      // regardless of exactly when in its cycle the loop currently is.
      acceptEvents = true;

      // FIXED: a healthy mechanism reconnects here -- this is the crux of
      // the fix. Poll (still under the clamped-delay reconnect loop) until
      // it does, rather than a single fixed sleep.
      await eventually(async () => {
        const health = await readHealth(bridge.url);
        assert.equal(health.sse_connected, true);
      }, 2_000);

      const healthAfterRecovery = await readHealth(bridge.url);
      assert.equal(
        healthAfterRecovery.sse_connected,
        true,
        "FIXED: the bridge reconnects on its own once signal-cli is healthy again -- runSignalCliEventLoop kept " +
          "retrying at a bounded long steady interval instead of returning for good at the old maxAttempts exit",
      );
      assert.ok(eventClients.size > 0, "a fresh SSE subscription was established post-recovery");
      assert.equal(
        healthAfterRecovery.reconnect_attempts,
        0,
        "connectSignalCliEvents' onOpen callback resets the attempt counter on the successful reconnect",
      );
    } finally {
      console.error = originalConsoleError;
      global.setTimeout = originalSetTimeout;
      await bridge.close();
      await new Promise<void>((resolve, reject) => {
        for (const client of eventClients) client.end();
        server.close((error) => (error ? reject(error) : resolve()));
      });
    }
  },
);
