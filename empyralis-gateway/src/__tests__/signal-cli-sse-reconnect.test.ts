import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { startSignalCliBridge } from "../bridges/signal-cli-bridge";

// ---------------------------------------------------------------------------
// Reliability audit finding (docs/design/reliability-audit-2-channels.md):
// "signal-cli is polled over HTTP/SSE by the gateway with ZERO reconnect/
// backoff (hardcoded to 0 attempts), and SSE drop failures are silently
// swallowed" (signal-cli-bridge.ts's connectSignalCliEvents call site used
// to be `.catch(() => undefined)`, with no retry at all).
//
// These tests exercise the fix directly against the real bridge: a fake
// signal-cli daemon that can actively drop its own SSE stream on demand,
// and a real HTTP `/health` read on the bridge to observe the reconnect
// state it now tracks and reports (sse_connected / reconnect_attempts /
// sse_last_error), using the SAME shared backoff util
// (foundation/reconnect-utils.ts's DEFAULT_RECONNECT_POLICY) Telegram/
// WhatsApp already use for their own socket reconnects.
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
      await new Promise((resolve) => setTimeout(resolve, 25));
    }
  }
  if (lastError instanceof Error) {
    throw lastError;
  }
  throw new Error("Timed out waiting for assertion.");
}

async function startFakeSignalCliDaemon(): Promise<{
  url: string;
  close: () => Promise<void>;
  eventClientCount: () => number;
  /** Actively ends every currently-open /api/v1/events SSE response --
   *  simulates signal-cli itself dropping the stream (process restart,
   *  network blip, etc) without tearing down the whole fake daemon. */
  dropEventClients: () => void;
}> {
  const eventClients = new Set<http.ServerResponse>();
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    if (request.method === "GET" && url.pathname === "/api/v1/check") {
      response.writeHead(200, { "content-type": "text/plain" });
      response.end("OK");
      return;
    }
    if (request.method === "GET" && url.pathname === "/api/v1/events") {
      response.writeHead(200, {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
      });
      // Node's http server buffers headers until the first body write by
      // default -- for a stream that may sit open with no data for a while
      // (exactly this test's "connected but idle" scenarios), that would
      // leave the CLIENT's fetch() promise unresolved indefinitely, since
      // fetch() only resolves once headers are actually received. Force
      // them out immediately, matching how a real SSE server (and a real
      // signal-cli daemon) behaves.
      response.flushHeaders();
      eventClients.add(response);
      request.on("close", () => {
        eventClients.delete(response);
      });
      return;
    }
    response.writeHead(404, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "not_found" }));
  });

  await new Promise<void>((resolve) => {
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  return {
    url: `http://127.0.0.1:${port}`,
    close: () => new Promise<void>((resolve, reject) => {
      for (const client of eventClients) {
        client.end();
      }
      server.close((error) => (error ? reject(error) : resolve()));
    }),
    eventClientCount: () => eventClients.size,
    dropEventClients: () => {
      for (const client of eventClients) {
        client.end();
      }
      eventClients.clear();
    },
  };
}

async function readHealth(bridgeUrl: string): Promise<Record<string, unknown>> {
  const response = await fetch(`${bridgeUrl}/health`);
  return response.json() as Promise<Record<string, unknown>>;
}

test("the SSE stream connects on startup and /health reports it as connected with zero reconnect attempts", async () => {
  const signalCli = await startFakeSignalCliDaemon();
  const bridge = await startSignalCliBridge({ signalCliRpcUrl: signalCli.url, account: "+15551234567" });
  try {
    await eventually(() => {
      assert.equal(signalCli.eventClientCount(), 1);
    });
    // sse_connected flips to true inside the bridge's own onOpen callback,
    // which fires once ITS fetch() call sees a response -- a beat after the
    // fake daemon's server-side handler has already added the response to
    // eventClients above. Poll /health rather than reading it once right
    // after the eventClientCount check, to avoid asserting mid-race.
    await eventually(async () => {
      const health = await readHealth(bridge.url);
      assert.equal(health.sse_connected, true);
      assert.equal(health.reconnect_attempts, 0);
      assert.ok(!(health.issues as string[]).includes("signal_cli_sse_disconnected"));
    });
  } finally {
    await bridge.close();
    await signalCli.close();
  }
});

test("a dropped SSE stream is surfaced (not swallowed) and automatically reconnected with backoff", async () => {
  const signalCli = await startFakeSignalCliDaemon();
  const bridge = await startSignalCliBridge({ signalCliRpcUrl: signalCli.url, account: "+15551234567" });
  const originalConsoleError = console.error;
  const loggedLines: string[] = [];
  console.error = (...args: unknown[]) => {
    loggedLines.push(args.map(String).join(" "));
  };
  try {
    // Wait for the bridge's OWN onOpen to have fired (not just the daemon's
    // server-side accept) before dropping anything, so the before/after
    // reconnect_attempts comparison below is meaningful.
    await eventually(async () => {
      const health = await readHealth(bridge.url);
      assert.equal(health.sse_connected, true);
    });

    // Simulate signal-cli itself dropping the connection (process restart,
    // network blip) -- previously this rejection/stream-end was swallowed
    // via `.catch(() => undefined)` with no visible trace anywhere.
    signalCli.dropEventClients();

    // The drop must be visible on /health -- proving it's surfaced rather
    // than silently absorbed while the reconnect loop waits to retry.
    await eventually(async () => {
      const droppedHealth = await readHealth(bridge.url);
      assert.equal(droppedHealth.sse_connected, false);
      assert.ok(
        (droppedHealth.issues as string[]).includes("signal_cli_sse_disconnected"),
        "a dropped SSE stream must be reflected in /health's issues, not just internal state",
      );
    }, 1_000);
    assert.ok(
      loggedLines.some((line) => line.includes("SSE event stream dropped")),
      "a dropped SSE stream must be logged, not silently swallowed",
    );

    // The shared bounded-backoff reconnect loop should re-establish the
    // stream on its own -- same policy Telegram/WhatsApp use
    // (DEFAULT_RECONNECT_POLICY: 1s initial delay), well within this test's
    // timeout.
    await eventually(() => {
      assert.equal(signalCli.eventClientCount(), 1);
    }, 5_000);
    await eventually(async () => {
      const recoveredHealth = await readHealth(bridge.url);
      assert.equal(recoveredHealth.sse_connected, true);
      assert.equal(recoveredHealth.reconnect_attempts, 0, "a successful reconnect must reset the attempt counter, same as Telegram's/WhatsApp's own reconnectAttempts = 0 on success");
      assert.ok(!(recoveredHealth.issues as string[]).includes("signal_cli_sse_disconnected"));
    });
  } finally {
    console.error = originalConsoleError;
    await bridge.close();
    await signalCli.close();
  }
});

test("while signal-cli is completely unreachable, /health's reconnect_attempts climbs instead of staying hardcoded at 0", async () => {
  // Point the bridge at a port nothing is listening on -- every SSE connect
  // attempt fails immediately (ECONNREFUSED), driving the reconnect loop's
  // attempt counter up exactly like a real prolonged outage would. Bind a
  // throwaway server to grab a genuinely free port, then close it
  // immediately -- more robust than a hardcoded magic port number, which
  // could collide with something else already listening in CI.
  const probe = http.createServer();
  const deadPort = await new Promise<number>((resolve) => {
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      resolve(typeof address === "object" && address ? address.port : 0);
    });
  });
  await new Promise<void>((resolve) => probe.close(() => resolve()));
  const bridge = await startSignalCliBridge({
    signalCliRpcUrl: `http://127.0.0.1:${deadPort}`,
    account: "+15551234567",
  });
  const originalConsoleError = console.error;
  console.error = () => undefined;
  try {
    await eventually(async () => {
      const health = await readHealth(bridge.url);
      assert.equal(health.sse_connected, false);
      assert.ok(Number(health.reconnect_attempts) >= 1, "reconnect_attempts must climb past 0 while genuinely unreachable, not stay hardcoded");
    }, 4_000);
  } finally {
    console.error = originalConsoleError;
    await bridge.close();
  }
});
