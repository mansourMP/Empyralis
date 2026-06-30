/**
 * Shared credential redaction for personal channel state.
 *
 * Deep-clones the state object, then recursively walks all keys.
 * Keys listed in `stringKeys` are replaced with "[REDACTED]".
 * Keys listed in `objectKeys` (when their value is a non-null, non-array object)
 * are replaced with `{ redacted: true }`.
 */
export function redactCredentials(
  state: Record<string, unknown>,
  stringKeys: readonly string[],
  objectKeys: readonly string[],
): Record<string, unknown> {
  const redacted = JSON.parse(JSON.stringify(state)) as Record<string, unknown>;

  function walk(obj: Record<string, unknown>): void {
    for (const [key, value] of Object.entries(obj)) {
      if (stringKeys.includes(key)) {
        obj[key] = "[REDACTED]";
      } else if (objectKeys.includes(key) && value && typeof value === "object") {
        obj[key] = { redacted: true };
      } else if (value && typeof value === "object" && !Array.isArray(value)) {
        walk(value as Record<string, unknown>);
      }
    }
  }

  walk(redacted);
  return redacted;
}
