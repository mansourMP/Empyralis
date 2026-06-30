#!/usr/bin/env bash
set -euo pipefail

FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:4000/api/v1}"
USER_AGENT="${USER_AGENT:-tauri}"

tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/empyralis-frontend-chat-smoke.XXXXXX")"
cookiejar="${tmpdir}/cookies.txt"
trap 'rm -rf "$tmpdir"' EXIT

email="codex.chat.$(date +%s)@local.test"
password='CodexChat234'

common_headers=(
  -H "Origin: ${FRONTEND_URL}"
  -H "User-Agent: ${USER_AGENT}"
)

signup_payload="$(jq -n \
  --arg email "$email" \
  --arg password "$password" \
  --arg name 'Codex Chat Smoke' \
  '{email:$email,password:$password,name:$name}')"

curl -sS -X POST "${BACKEND_URL}/auth/signup" \
  -H 'Content-Type: application/json' \
  -d "$signup_payload" \
  > "${tmpdir}/signup.json"

jq -e '.token | type == "string" and length > 0' "${tmpdir}/signup.json" >/dev/null

login_payload="$(jq -n \
  --arg email "$email" \
  --arg password "$password" \
  '{email:$email,password:$password,return_to:"/"}')"

curl -sS -c "$cookiejar" -b "$cookiejar" -X POST "${FRONTEND_URL}/api/control-plane/auth/login" \
  "${common_headers[@]}" \
  -H 'Content-Type: application/json' \
  -d "$login_payload" \
  > "${tmpdir}/login.json"

jq -e '.ok == true and .auth_type == "bearer" and .admin == true' "${tmpdir}/login.json" >/dev/null

curl -sS -c "$cookiejar" -b "$cookiejar" "${FRONTEND_URL}/api/control-plane/session" \
  "${common_headers[@]}" \
  > "${tmpdir}/session.json"

jq -e '.ok == true' "${tmpdir}/session.json" >/dev/null

curl -sS -c "$cookiejar" -b "$cookiejar" "${FRONTEND_URL}/api/control-plane/providers/runtime-availability?workspace_id=default" \
  "${common_headers[@]}" \
  > "${tmpdir}/runtime-availability.json"

openai_ready_count="$(jq -r '[.items[] | select((.provider // "") == "openai" and .ready == true)] | length' "${tmpdir}/runtime-availability.json")"
if [[ "${openai_ready_count}" == "0" ]]; then
  curl -sS -c "$cookiejar" -b "$cookiejar" "${FRONTEND_URL}/connect-ai?returnTo=%2F" \
    "${common_headers[@]}" \
    > "${tmpdir}/connect-ai.html"
  grep -qi 'Connect AI account' "${tmpdir}/connect-ai.html"
  echo "PASS frontend chat gated correctly (no verified managed provider)"
  echo "  email: ${email}"
  echo "  frontend: ${FRONTEND_URL}"
  exit 0
fi

run_payload="$(jq -n --arg email "$email" '{
  tenant_id:"default",
  workspace_id:"default",
  session_id:"frontend-chat-smoke",
  channel:"web",
  actor:{type:"user", id:$email, display_name:$email},
  message:"Reply with exactly READY and nothing else.",
  execution_mode:"durable",
  response_mode:"artifact",
  context_hints:{
    engine:"orion",
    agent_role:"orchestrator",
    provider:"openai",
    metadata:{
      origin:"frontend_chat_smoke",
      trust_mode:"auto",
      connection_mode:"managed",
      execution_target:"cloud",
      source:"launch_gate"
    }
  }
}')"

curl -sS -c "$cookiejar" -b "$cookiejar" -X POST "${FRONTEND_URL}/api/turn" \
  "${common_headers[@]}" \
  -H 'Content-Type: application/json' \
  -d "$run_payload" \
  > "${tmpdir}/run-start.json"

run_id="$(jq -r '.run_id // empty' "${tmpdir}/run-start.json")"
if [[ -z "$run_id" ]]; then
  echo "Chat run start failed:" >&2
  cat "${tmpdir}/run-start.json" >&2 || true
  exit 1
fi

run_status=""
for _ in $(seq 1 40); do
  curl -sS -c "$cookiejar" -b "$cookiejar" "${FRONTEND_URL}/api/runs/${run_id}" \
    "${common_headers[@]}" \
    > "${tmpdir}/run-detail.json"
  run_status="$(jq -r '.status // empty' "${tmpdir}/run-detail.json")"
  case "$run_status" in
    completed|waiting_for_input|failed)
      break
      ;;
  esac
  sleep 1
done

if [[ "$run_status" != "completed" ]]; then
  echo "Chat run did not complete:" >&2
  cat "${tmpdir}/run-detail.json" >&2 || true
  exit 1
fi

jq -e '
  (
    (.result_summary // "") |
    ascii_downcase |
    contains("ready")
  )
  or
  (
    [(.events // [])[]?.message // ""] |
    map(ascii_downcase | contains("ready")) |
    any
  )
' "${tmpdir}/run-detail.json" >/dev/null

echo "PASS frontend chat managed-provider smoke"
echo "  email: ${email}"
echo "  run_id: ${run_id}"
echo "  frontend: ${FRONTEND_URL}"
