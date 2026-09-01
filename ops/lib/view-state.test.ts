/**
 * planOperatorView — proven against the real rule, same discipline as
 * frontend/lib/workspace/fleet/platform-activation.test.ts. The negative
 * assertions matter most: a failed/forbidden fetch must never reach `ready`.
 *
 * Run: npx tsx lib/view-state.test.ts
 */

import { getErrorMessage, planOperatorView } from "./view-state";

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

type Payload = { total: number };

assert(
  planOperatorView<Payload>({ loading: true, status: null, error: null, data: null }).kind === "loading",
  "still loading, no verdict yet",
);

assert(
  planOperatorView<Payload>({ loading: false, status: 403, error: "Operator access required.", data: null }).kind === "forbidden",
  "a 403 reads as forbidden, not a generic error",
);
// 401 and 403 are DIFFERENT FACTS and may never share one signal. Collapsing
// them told a signed-out operator that their account was not entitled -- false,
// and unactionable, since the page offered no way to sign in. 2026-09-02.
assert(
  planOperatorView<Payload>({ loading: false, status: 401, error: null, data: null }).kind === "signedOut",
  "a 401 is 'nobody is signed in', never 'your account lacks access'",
);
assert(
  planOperatorView<Payload>({ loading: false, status: 401, error: null, data: null }).kind !== "forbidden",
  "a 401 must NOT reach the forbidden copy -- that copy accuses the reader's account",
);
assert(
  planOperatorView<Payload>({ loading: false, status: 403, error: null, data: null }).kind !== "signedOut",
  "a 403 must NOT offer a sign-in link -- signing in again fixes nothing",
);
assert(
  planOperatorView<Payload>({ loading: true, status: 403, error: null, data: null }).kind === "loading",
  "loading wins over a stale status left over from a previous fetch",
);

{
  const state = planOperatorView<Payload>({
    loading: false,
    status: 503,
    error: "Control-plane database is unavailable; operator console data cannot be verified.",
    data: null,
  });
  assert(state.kind === "error", "a 503 (pool unavailable) is an error, never a silent empty/zero payload");
  assert(
    state.kind === "error" && state.message.includes("Control-plane database is unavailable"),
    "the real server message reaches the render state, not a generic string",
  );
}

{
  const state = planOperatorView<Payload>({
    loading: false,
    status: 500,
    error: "operator console query (overview) ran with app.rls_bypass=unset",
    data: null,
  });
  assert(state.kind === "error", "a broken RLS-bypass canary surfaces as an error, not ready-with-zeros");
}

assert(
  planOperatorView<Payload>({ loading: false, status: null, error: "Failed to fetch", data: null }).kind === "error",
  "a request that never got a response (status null) is still an error, not forbidden and not ready",
);

{
  // The exact regression this whole app exists to prevent: a 200 whose body
  // genuinely carries zeros must still reach `ready`. Distinguishing "empty"
  // from "broken" is the BACKEND's job (the rls_bypass canary); once a data
  // object reaches this function at all, it is already verified.
  const state = planOperatorView<Payload>({ loading: false, status: 200, error: null, data: { total: 0 } });
  assert(state.kind === "ready", "a genuinely zeroed payload still renders as ready data, not hidden as an error");
}

{
  const state = planOperatorView<Payload>({ loading: false, status: 200, error: null, data: { total: 105 } });
  assert(state.kind === "ready", "a normal successful fetch reaches ready");
  assert(state.kind === "ready" && state.data.total === 105, "the real payload passes through untouched");
}

assert(
  planOperatorView<Payload>({ loading: false, status: 200, error: null, data: null }).kind === "error",
  "a 200 with no body is still an error, never fabricated as ready with nothing in it",
);

// ── getErrorMessage ──────────────────────────────────────────────────────

assert(getErrorMessage({ detail: "Operator access required." }, "fallback") === "Operator access required.", "a string detail passes through");
assert(getErrorMessage({ error: "not found" }, "fallback") === "not found", "a string error field is used when detail is absent");
assert(getErrorMessage({ detail: [{ type: "missing", msg: "field required" }] }, "fallback") === "fallback", "a non-string detail (FastAPI validation-error array) never coerces to [object Object]");
assert(getErrorMessage(null, "fallback") === "fallback", "no body at all falls back");
assert(getErrorMessage({}, "fallback") === "fallback", "an empty object falls back");
assert(getErrorMessage({ detail: "  " }, "fallback") === "fallback", "a whitespace-only detail is treated as absent");

if (failed > 0) {
  console.error(`\n${failed} failed, ${passed} passed`);
  process.exit(1);
} else {
  console.log(`${passed} passed`);
}
