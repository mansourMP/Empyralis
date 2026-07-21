/**
 * Dotted-numeric version compare for gateway self-update. Mirrors
 * is_newer_gateway_version() in server_modules/gateway_self_update_service.py
 * — keep the two in sync if this logic changes.
 */
function versionParts(value: string): number[] | null {
  let cleaned = String(value || "").trim();
  if (/^v/i.test(cleaned)) {
    cleaned = cleaned.slice(1);
  }
  if (!cleaned) {
    return null;
  }
  const parts: number[] = [];
  for (const segment of cleaned.split(".")) {
    const digits = segment.replace(/[^0-9]/g, "");
    if (!digits) {
      return null;
    }
    parts.push(Number.parseInt(digits, 10));
  }
  return parts.length ? parts : null;
}

/** True when `candidate` is a strictly newer dotted-numeric version than
 *  `current`. Malformed input on either side returns false — "cannot tell"
 *  reads as no update available rather than a false positive. */
export function isNewerGatewayVersion(current: string, candidate: string): boolean {
  const a = versionParts(current);
  const b = versionParts(candidate);
  if (!a || !b) {
    return false;
  }
  const length = Math.max(a.length, b.length);
  for (let i = 0; i < length; i += 1) {
    const av = a[i] ?? 0;
    const bv = b[i] ?? 0;
    if (bv > av) return true;
    if (bv < av) return false;
  }
  return false;
}
