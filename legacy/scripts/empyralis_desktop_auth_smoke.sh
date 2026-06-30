#!/usr/bin/env bash
set -euo pipefail

FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:4000/api/v1}"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/empyralis-auth-smoke.XXXXXX")"
trap 'rm -rf "$tmpdir"' EXIT

email="codex.auth.$(date +%s)@local.test"
password='CodexTest!234'

signup_json="$tmpdir/signup.json"
login_json="$tmpdir/login.json"
consume_headers="$tmpdir/consume.headers"
consume_body="$tmpdir/consume.body"

signup_payload="$(jq -n \
  --arg email "$email" \
  --arg password "$password" \
  --arg name 'Codex Auth Smoke' \
  '{email:$email,password:$password,name:$name}')"

curl -sS -X POST "${BACKEND_URL}/auth/signup" \
  -H 'Content-Type: application/json' \
  -d "$signup_payload" \
  > "$signup_json"

jq -e 'has("token") and (.token | type == "string") and (.token | length > 0)' "$signup_json" >/dev/null

login_payload="$(jq -n \
  --arg email "$email" \
  --arg password "$password" \
  '{email:$email,password:$password,desktop_handoff:true,return_to:"/"}')"

curl -sS -X POST "${FRONTEND_URL}/api/control-plane/auth/login" \
  -H 'Content-Type: application/json' \
  -H "Origin: ${FRONTEND_URL}" \
  -d "$login_payload" \
  > "$login_json"

jq -e '.ok == true and .desktop_handoff == true and (.redirect_to | type == "string")' "$login_json" >/dev/null

curl -sS -D "$consume_headers" -o "$consume_body" -X POST "${FRONTEND_URL}/api/control-plane/auth/desktop/consume" \
  -H "Origin: ${FRONTEND_URL}" \
  -H 'User-Agent: tauri'

status_code="$(awk 'NR==1 {print $2}' "$consume_headers")"
if [[ "$status_code" != "200" ]]; then
  echo "Desktop auth consume failed with status ${status_code}." >&2
  cat "$consume_body" >&2 || true
  exit 1
fi

grep -qi '^set-cookie: orion_cp_admin=' "$consume_headers"
grep -qi '^set-cookie: orion_cp_session=' "$consume_headers"
jq -e '.ok == true and .auth_type == "bearer" and .admin == true' "$consume_body" >/dev/null

echo "PASS desktop auth handoff"
echo "  email: ${email}"
echo "  frontend: ${FRONTEND_URL}"
echo "  backend: ${BACKEND_URL}"
