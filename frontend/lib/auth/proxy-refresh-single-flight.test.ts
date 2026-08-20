/**
 * Proves the mechanism proxy.ts now relies on to stop concurrent
 * page/RSC navigations from racing the backend's single-use refresh-token
 * rotation — see proxy-refresh-single-flight.ts's own header comment for
 * the full incident (a live disposable-stack capture: 5 concurrent
 * qualifying GETs producing 1 winning refresh + 4 losing 401s, escalating
 * into a >60s 401/429 storm under sustained concurrency).
 *
 * Run: npx tsx lib/auth/proxy-refresh-single-flight.test.ts
 */

import assert from "node:assert/strict";

import {
  _resetProxyRefreshSingleFlightForTests,
  singleFlightedProxyRefresh,
} from "./proxy-refresh-single-flight";

let failures = 0;

async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  _resetProxyRefreshSingleFlightForTests();
  try {
    await fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failures += 1;
    console.error(`FAIL - ${name}`);
    console.error(error);
  }
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

async function main() {
  await test(
    "concurrent calls with the SAME token collapse into exactly one run()",
    async () => {
      let callCount = 0;
      const gate = deferred<string>();
      const run = () => {
        callCount += 1;
        return gate.promise;
      };

      // Five "concurrent proxy invocations" — the exact shape of five
      // near-simultaneous qualifying GETs (RSC prefetches / router.refresh()
      // polls) all deciding independently that the access token needs a
      // refresh, all carrying the same pre-rotation refresh_token cookie.
      const inFlight = [1, 2, 3, 4, 5].map(() =>
        singleFlightedProxyRefresh("same-refresh-token", run),
      );

      // run() must have been invoked exactly once even though five callers
      // are all awaiting — this is the assertion that would fail on the
      // pre-fix code (each proxy invocation called fetch() independently).
      assert.equal(callCount, 1, "run() must be invoked exactly once for concurrent callers sharing a token");

      gate.resolve("winning-set-cookie-headers");
      const results = await Promise.all(inFlight);
      for (const result of results) {
        assert.equal(result, "winning-set-cookie-headers", "every concurrent caller must observe the SAME winning result");
      }
    },
  );

  await test(
    "DIFFERENT tokens are never coalesced together",
    async () => {
      let callCount = 0;
      const run = () => {
        callCount += 1;
        return Promise.resolve(`result-${callCount}`);
      };

      const a = await singleFlightedProxyRefresh("token-a", run);
      const b = await singleFlightedProxyRefresh("token-b", run);

      assert.equal(callCount, 2, "distinct token values must each get their own run() call");
      assert.notEqual(a, b, "distinct token values must not share a result");
    },
  );

  await test(
    "a SECOND wave (after the first attempt settles) issues a NEW run()",
    async () => {
      let callCount = 0;
      const run = () => {
        callCount += 1;
        return Promise.resolve(callCount);
      };

      const first = await singleFlightedProxyRefresh("token", run);
      const second = await singleFlightedProxyRefresh("token", run);

      assert.equal(first, 1);
      assert.equal(second, 2, "once the in-flight entry is cleared, the SAME token may refresh again (e.g. the next rotation)");
      assert.equal(callCount, 2);
    },
  );

  await test(
    "a rejected run() clears the in-flight entry so a retry is possible",
    async () => {
      let callCount = 0;
      const run = () => {
        callCount += 1;
        if (callCount === 1) {
          return Promise.reject(new Error("upstream unreachable"));
        }
        return Promise.resolve("recovered");
      };

      await assert.rejects(() => singleFlightedProxyRefresh("token", run));
      const recovered = await singleFlightedProxyRefresh("token", run);
      assert.equal(recovered, "recovered", "a failed attempt must not permanently wedge the token's single-flight slot");
      assert.equal(callCount, 2);
    },
  );

  if (failures > 0) {
    console.error(`\n${failures} test(s) failed.`);
    process.exit(1);
  }
  console.log("\nAll proxy-refresh-single-flight tests passed.");
}

void main();
