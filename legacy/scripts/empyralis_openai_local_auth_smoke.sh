#!/usr/bin/env bash
set -euo pipefail

FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:4000/api/v1}"
USER_AGENT="${USER_AGENT:-tauri}"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/empyralis-openai-local-auth-smoke.XXXXXX")"
cookiejar="${tmpdir}/cookies.txt"
trap 'rm -rf "$tmpdir"' EXIT

email="codex.localauth.$(date +%s)@local.test"
password='CodexLocalAuth234'

common_headers=(
  -H "Origin: ${FRONTEND_URL}"
  -H "User-Agent: ${USER_AGENT}"
)

signup_payload="$(jq -n \
  --arg email "$email" \
  --arg password "$password" \
  --arg name 'Codex Local Auth Smoke' \
  '{email:$email,password:$password,name:$name}')"

curl -sS -X POST "${BACKEND_URL}/auth/signup" \
  -H 'Content-Type: application/json' \
  -d "$signup_payload" \
  > "${tmpdir}/signup.json"

jq -e '.token | type == "string" and length > 0' "${tmpdir}/signup.json" >/dev/null

login_payload="$(jq -n \
  --arg email "$email" \
  --arg password "$password" \
  '{email:$email,password:$password,return_to:"/connect-ai"}')"

curl -sS -c "$cookiejar" -b "$cookiejar" -X POST "${FRONTEND_URL}/api/control-plane/auth/login" \
  "${common_headers[@]}" \
  -H 'Content-Type: application/json' \
  -d "$login_payload" \
  > "${tmpdir}/login.json"

jq -e '.ok == true' "${tmpdir}/login.json" >/dev/null

curl -sS -c "$cookiejar" -b "$cookiejar" "${FRONTEND_URL}/api/control-plane/providers/openai/local-auth/status" \
  "${common_headers[@]}" \
  > "${tmpdir}/status.json"

jq -e '.available == true and .importable == true and .has_access_token == true' "${tmpdir}/status.json" >/dev/null

curl -sS -c "$cookiejar" -b "$cookiejar" -X POST "${FRONTEND_URL}/api/control-plane/providers/openai/local-auth/import" \
  "${common_headers[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"workspace_id":"default","enable_runtime":true}' \
  > "${tmpdir}/import.json"

jq -e '.ok == true and .provider == "openai" and (.credential_id | type == "string") and (.credential_id | length > 0)' "${tmpdir}/import.json" >/dev/null

credential_id="$(jq -r '.credential_id' "${tmpdir}/import.json")"

curl -sS -c "$cookiejar" -b "$cookiejar" -X POST "${FRONTEND_URL}/api/control-plane/credentials/${credential_id}/test?workspace_id=default" \
  "${common_headers[@]}" \
  > "${tmpdir}/test.json"

jq -e '.provider == "openai" and (.ok | type == "boolean") and (.message | type == "string")' "${tmpdir}/test.json" >/dev/null

enabled_runtime="$(jq -r '.enabled_for_runtime' "${tmpdir}/import.json")"
credential_ok="$(jq -r '.ok' "${tmpdir}/test.json")"
credential_status="$(jq -r '.status // ""' "${tmpdir}/test.json")"

echo "PASS openai local auth import smoke"
echo "  email: ${email}"
echo "  credential_id: ${credential_id}"
echo "  enabled_for_runtime: ${enabled_runtime}"
echo "  credential_ok: ${credential_ok}"
echo "  credential_status: ${credential_status}"
echo "  frontend: ${FRONTEND_URL}"
