// A backend error body's `detail`/`error` field is not always a string —
// FastAPI's own validation-error shape is `{"detail": [{"type":...,
// "loc":..., "msg":...}]}`, an array of objects, and some routes return a
// structured `error`/`detail` for other reasons. Passing that straight into
// `new Error(...)` (or `String(...)`) coerces it via ToString into the
// literal text "[object Object]", which then renders verbatim as the
// user-facing error — worse than a generic message, because it leaks
// internals instead of saying nothing useful.
//
// `getErrorMessage` only ever returns a STRING: a non-string `detail`/
// `error` is treated as absent and the caller's `fallback` takes over,
// instead of the raw shape reaching the screen. Every call site across the
// wizard/fleet surfaces that used to build its own `Error` out of
// `data?.error || data?.detail || …` should go through this instead of
// reinventing the same guard (or, worse, skipping it).
export function getErrorMessage(data: unknown, fallback: string): string {
  if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    if (typeof record.detail === "string" && record.detail.trim()) return record.detail;
    if (typeof record.error === "string" && record.error.trim()) return record.error;
  }
  return fallback;
}
