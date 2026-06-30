#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

API_URL="${EMPYRALIS_API_URL:-${ORION_API_URL:-http://127.0.0.1:8001}}"
STACK_RUNTIME_KEY_FILE="${ROOT_DIR}/.orion-stack/runtime_key"
RUNTIME_KEY_RAW="${RUNTIME_KEY:-${EMPYRALIS_API_KEY:-${ORION_API_KEY:-}}}"
START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_empyralis_local_stack.sh"
if [[ ! -x "${START_STACK_SCRIPT}" ]]; then
  START_STACK_SCRIPT="${ROOT_DIR}/scripts/start_orion_local_stack.sh"
fi
SCREENSHOT_SMOKE="${EMPYRALIS_SMOKE_SCREENSHOT:-0}"
TMP_DIR="$(mktemp -d)"
FAILURES=0
PASSES=0
BROWSER_SESSION_SERVER_PID=""
STATE_HOME="${EMPYRALIS_STATE_HOME:-$HOME/.empyralis/state}"
VALIDATION_REPORT_DIR="${ORION_VALIDATION_REPORT_DIR:-${STATE_HOME}/validation}"
VALIDATION_REPORT_FILE="${VALIDATION_REPORT_DIR}/latest_core_smoke.json"
VALIDATION_CHECKS_FILE="${TMP_DIR}/checks.tsv"
ORCHESTRATOR_ATTEMPTS="${EMPYRALIS_SMOKE_ORCHESTRATOR_ATTEMPTS:-360}"
SMOKE_POLL_ATTEMPTS="${EMPYRALIS_SMOKE_POLL_ATTEMPTS:-60}"
SMOKE_POLL_SLEEP="${EMPYRALIS_SMOKE_POLL_SLEEP:-5}"

cleanup() {
  if [[ -n "${BROWSER_SESSION_SERVER_PID}" ]]; then
    kill "${BROWSER_SESSION_SERVER_PID}" >/dev/null 2>&1 || true
    wait "${BROWSER_SESSION_SERVER_PID}" 2>/dev/null || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

normalize_runtime_key() {
  printf "%s" "$1" | tr -d '[:space:]'
}

RUNTIME_KEY="$(normalize_runtime_key "${RUNTIME_KEY_RAW}")"
if [[ -z "${RUNTIME_KEY}" && -f "${STACK_RUNTIME_KEY_FILE}" ]]; then
  RUNTIME_KEY="$(normalize_runtime_key "$(cat "${STACK_RUNTIME_KEY_FILE}" 2>/dev/null || true)")"
fi
if [[ -z "${RUNTIME_KEY}" ]]; then
  echo "Missing runtime key. Set RUNTIME_KEY or start the local stack first." >&2
  exit 1
fi

headers=(-H "X-API-Key: ${RUNTIME_KEY}")
json_headers=(-H "X-API-Key: ${RUNTIME_KEY}" -H "Content-Type: application/json")

pass() {
  PASSES=$((PASSES + 1))
  printf 'pass\t%s\n' "$1" >> "${VALIDATION_CHECKS_FILE}"
  echo "PASS  $1"
}

fail() {
  FAILURES=$((FAILURES + 1))
  printf 'fail\t%s\n' "$1" >> "${VALIDATION_CHECKS_FILE}"
  echo "FAIL  $1"
}

info() {
  echo "INFO  $1"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Required command not found: $1" >&2
    exit 1
  }
}

require_cmd curl
require_cmd jq
require_cmd python3

browser_session_port() {
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

ensure_runtime() {
  local code
  code="$(curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 4 "${headers[@]}" "${API_URL}/health" 2>/dev/null || true)"
  if [[ "${code}" == "200" ]]; then
    return 0
  fi
  if [[ -x "${START_STACK_SCRIPT}" ]]; then
    info "Runtime offline; starting local stack."
    RUNTIME_KEY="${RUNTIME_KEY}" bash "${START_STACK_SCRIPT}" >/tmp/empyralis-core-smoke-start.log 2>&1 || {
      echo "Failed to start stack. See /tmp/empyralis-core-smoke-start.log" >&2
      exit 1
    }
    code="$(curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 8 "${headers[@]}" "${API_URL}/health" 2>/dev/null || true)"
    if [[ "${code}" == "200" ]]; then
      return 0
    fi
  fi
  echo "Runtime unreachable at ${API_URL}" >&2
  exit 1
}

api_get() {
  local path="$1"
  local url
  local tmp
  local status
  tmp="$(mktemp)"
  if [[ "${path}" == http://* || "${path}" == https://* ]]; then
    url="${path}"
  else
    url="${API_URL}${path}"
  fi
  status="$(curl -sS -o "${tmp}" -w "%{http_code}" "${headers[@]}" "${url}")" || {
    rm -f "${tmp}"
    return 1
  }
  if [[ "${status}" =~ ^2 ]]; then
    cat "${tmp}"
    rm -f "${tmp}"
    return 0
  fi
  rm -f "${tmp}"
  return 1
}

api_post() {
  local path="$1"
  local payload="$2"
  if [[ "${path}" == http://* || "${path}" == https://* ]]; then
    curl -fsS -X POST "${json_headers[@]}" -d "${payload}" "${path}"
  else
    curl -fsS -X POST "${json_headers[@]}" -d "${payload}" "${API_URL}${path}"
  fi
}

api_post_expect_status() {
  local path="$1"
  local payload="$2"
  local expected_status="$3"
  local tmp
  local status
  tmp="$(mktemp)"
  if [[ "${path}" == http://* || "${path}" == https://* ]]; then
    status="$(curl -sS -o "${tmp}" -w "%{http_code}" -X POST "${json_headers[@]}" -d "${payload}" "${path}")"
  else
    status="$(curl -sS -o "${tmp}" -w "%{http_code}" -X POST "${json_headers[@]}" -d "${payload}" "${API_URL}${path}")"
  fi
  if [[ "${status}" != "${expected_status}" ]]; then
    cat "${tmp}" >&2
    rm -f "${tmp}"
    return 1
  fi
  cat "${tmp}"
  rm -f "${tmp}"
}

poll_json_condition() {
  local path="$1"
  local attempts="${2:-30}"
  local sleep_secs="${3:-1}"
  local jq_condition="$4"
  local out_file="$5"
  local i
  for ((i=0; i<attempts; i++)); do
    if api_get "${path}" > "${out_file}" && jq -e "${jq_condition}" "${out_file}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${sleep_secs}"
  done
  api_get "${path}" > "${out_file}" 2>/dev/null || true
  return 1
}

read_run_history_snapshot() {
  local run_id="$1"
  local out_file="$2"
  local state_home="${EMPYRALIS_STATE_HOME:-$HOME/.empyralis/state}"
  local runtime_db="${ORION_RUNTIME_STATE_DB:-}"
  if [[ -z "${runtime_db}" ]]; then
    runtime_db="${state_home}/runtime/state.db"
  fi
  python3 - "${runtime_db}" "$run_id" "$out_file" <<'PY'
import sqlite3, sys
db_path, run_id, out_file = sys.argv[1:4]
try:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    row = cur.execute(
        "select snapshot_json from run_history where run_id = ?",
        (run_id,),
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        raise SystemExit(1)
    with open(out_file, "w", encoding="utf-8") as fh:
        fh.write(row[0])
except Exception:
    raise SystemExit(1)
PY
}

poll_run_snapshot_condition() {
  local run_id="$1"
  local attempts="${2:-30}"
  local sleep_secs="${3:-1}"
  local jq_condition="$4"
  local out_file="$5"
  local api_path="/runs/${run_id}"
  local i
  for ((i=0; i<attempts; i++)); do
    if api_get "${api_path}" > "${out_file}"; then
      if jq -e "${jq_condition}" "${out_file}" >/dev/null 2>&1; then
        return 0
      fi
      if read_run_history_snapshot "${run_id}" "${out_file}" && jq -e "${jq_condition}" "${out_file}" >/dev/null 2>&1; then
        return 0
      fi
    elif read_run_history_snapshot "${run_id}" "${out_file}" && jq -e "${jq_condition}" "${out_file}" >/dev/null 2>&1; then
      return 0
    fi
    sleep "${sleep_secs}"
  done
  if ! api_get "${api_path}" > "${out_file}" 2>/dev/null; then
    read_run_history_snapshot "${run_id}" "${out_file}" || true
  fi
  return 1
}

read_jq_program() {
  cat
}

urlencode() {
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"
}

poll_run_terminal() {
  local run_id="$1"
  local attempts="${2:-60}"
  local sleep_secs="${3:-1}"
  local out_file="$4"
  local i status
  for ((i=0; i<attempts; i++)); do
    if api_get "/runs/${run_id}" > "${out_file}"; then
      status="$(jq -r '.status // empty' "${out_file}")"
      case "${status}" in
        completed|failed|waiting_for_input)
          return 0
          ;;
      esac
    fi
    if read_run_history_snapshot "${run_id}" "${out_file}"; then
      status="$(jq -r '.status // empty' "${out_file}")"
      case "${status}" in
        completed|failed|waiting_for_input)
          return 0
          ;;
      esac
    fi
    sleep "${sleep_secs}"
  done
  return 1
}

poll_artifact_preview_ready() {
  local workspace_id="$1"
  local run_id="$2"
  local artifact_path="$3"
  local attempts="${4:-60}"
  local sleep_secs="${5:-1}"
  local artifacts_out="$6"
  local preview_out="$7"
  local artifact_api_path="/artifacts/workspace?workspace_id=${workspace_id}&history_limit=40"
  local i
  for ((i=0; i<attempts; i++)); do
    if api_get "${artifact_api_path}" > "${artifacts_out}" \
      && jq -e --arg rid "${run_id}" '.items | map(select(.run_id == $rid)) | length > 0' "${artifacts_out}" >/dev/null 2>&1 \
      && api_get "/artifacts/preview?path=$(urlencode "${artifact_path}")" > "${preview_out}" \
      && [[ "$(jq -r '.ok // false' "${preview_out}")" == "true" ]]; then
      return 0
    fi
    sleep "${sleep_secs}"
  done
  api_get "${artifact_api_path}" > "${artifacts_out}" 2>/dev/null || true
  if [[ -n "${artifact_path}" ]]; then
    api_get "/artifacts/preview?path=$(urlencode "${artifact_path}")" > "${preview_out}" 2>/dev/null || true
  fi
  return 1
}

ensure_runtime

echo "== Empyralis Core Smoke =="
echo "root: ${ROOT_DIR}"
echo "api:  ${API_URL}"
echo

health_json="${TMP_DIR}/health.json"
api_get "/health" > "${health_json}"
if [[ "$(jq -r '.ok' "${health_json}")" == "true" ]] && [[ "$(jq -r '.local_companion_enabled // false' "${health_json}")" == "true" ]]; then
  pass "A1 runtime health"
else
  fail "A1 runtime health"
fi

connectors_json="${TMP_DIR}/connectors.json"
if api_get "/connectors/vault?workspace_id=default" > "${connectors_json}"; then
  pass "A2 connector inventory"
else
  fail "A2 connector inventory"
fi

browser_site_dir="${TMP_DIR}/browser-session-site"
mkdir -p "${browser_site_dir}"
cat > "${browser_site_dir}/index.html" <<'HTML'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Session Smoke</title>
    <script>
      function syncState() {
        const params = new URLSearchParams(window.location.search);
        const seed = params.get('seed');
        if (seed) {
          localStorage.setItem('empyralis-smoke', seed);
        }
        const current = localStorage.getItem('empyralis-smoke') || 'unset';
        document.getElementById('state').textContent = current;
        document.title = current === 'unset' ? 'Session Smoke' : `Session ${current}`;
      }
      function persistValue() {
        const value = document.getElementById('search').value || 'unset';
        localStorage.setItem('empyralis-smoke', value);
        syncState();
      }
      function handleUpload() {
        const el = document.getElementById('upload');
        const file = el.files && el.files[0];
        if (!file) {
          document.getElementById('upload-state').textContent = 'no-file';
          return;
        }
        const reader = new FileReader();
        reader.onload = () => {
          document.getElementById('upload-state').textContent = String(reader.result || '').trim();
        };
        reader.readAsText(file);
      }
      window.addEventListener('DOMContentLoaded', syncState);
    </script>
  </head>
  <body>
    <h1 id="ready">Browser Session Smoke</h1>
    <input id="search" type="text" value="" />
    <select id="choice">
      <option value="alpha">Alpha</option>
      <option value="beta">Beta</option>
    </select>
    <input id="upload" type="file" onchange="handleUpload()" />
    <button id="go" onclick="persistValue()">Save</button>
    <a id="download-link" href="/download.txt" download="download.txt">Download file</a>
    <a id="popup-link" href="/popup.html" target="_blank" rel="noopener">Open popup</a>
    <div id="state">unset</div>
    <div id="upload-state">no-file</div>
    <iframe id="panel" src="/frame.html" title="Smoke frame"></iframe>
  </body>
</html>
HTML
cat > "${browser_site_dir}/frame.html" <<'HTML'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Frame Smoke</title>
    <script>
      function runFrameAction() {
        const value = document.getElementById('frame-search').value || 'unset';
        document.getElementById('frame-result').textContent = value;
      }
    </script>
  </head>
  <body>
    <input id="frame-search" type="text" value="" />
    <button id="frame-go" onclick="runFrameAction()">Apply</button>
    <div id="frame-result">unset</div>
  </body>
</html>
HTML
cat > "${browser_site_dir}/second.html" <<'HTML'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Second Page</title>
  </head>
  <body>
    <h1 id="second-ready">Second page ready</h1>
    <p id="second-copy">Tab smoke target</p>
  </body>
</html>
HTML
cat > "${browser_site_dir}/popup.html" <<'HTML'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Popup Page</title>
  </head>
  <body>
    <h1 id="popup-ready">Popup ready</h1>
    <p id="popup-copy">Popup smoke target</p>
  </body>
</html>
HTML
cat > "${browser_site_dir}/download.txt" <<'TXT'
Empyralis download smoke payload
TXT
browser_session_port_value="$(browser_session_port)"
python3 -m http.server "${browser_session_port_value}" --bind 127.0.0.1 --directory "${browser_site_dir}" > "${TMP_DIR}/browser-session-http.log" 2>&1 &
BROWSER_SESSION_SERVER_PID=$!
sleep 1
browser_session_url="http://127.0.0.1:${browser_session_port_value}/index.html"

ensure_browser_session_server() {
  if curl -fsS --max-time 3 "${browser_session_url}" >/dev/null 2>&1; then
    return 0
  fi
  if [[ -n "${BROWSER_SESSION_SERVER_PID:-}" ]]; then
    kill "${BROWSER_SESSION_SERVER_PID}" >/dev/null 2>&1 || true
    wait "${BROWSER_SESSION_SERVER_PID}" >/dev/null 2>&1 || true
  fi
  python3 -m http.server "${browser_session_port_value}" --bind 127.0.0.1 --directory "${browser_site_dir}" > "${TMP_DIR}/browser-session-http.log" 2>&1 &
  BROWSER_SESSION_SERVER_PID=$!
  sleep 1
  curl -fsS --max-time 3 "${browser_session_url}" >/dev/null
}

read_payload="$(jq -nc '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test local file read via private assistant.",
  agent_role:"private-assistant",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {tool:"filesystem.read_write", mode:"read", path:"server.py"}
      ],
      continue_on_error:false
    }
  }
}')"
read_start_json="${TMP_DIR}/read-start.json"
api_post "/turn" "${read_payload}" > "${read_start_json}"
read_run_id="$(jq -r '.run_id // empty' "${read_start_json}")"
read_run_json="${TMP_DIR}/read-run.json"
if [[ -n "${read_run_id}" ]] && poll_run_terminal "${read_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${read_run_json}"; then
  read_status="$(jq -r '.status // empty' "${read_run_json}")"
  read_role="$(jq -r '.context.metadata.agent_role // .agent_role // empty' "${read_run_json}")"
  read_artifact="$(jq -r '(.result_data.outputs.artifacts // [])[0].file_path // empty' "${read_run_json}")"
  if [[ "${read_status}" == "completed" && "${read_role}" == "private-assistant" && -n "${read_artifact}" ]]; then
    pass "A3 agent-owned local file run"
  else
    fail "A3 agent-owned local file run"
  fi
else
  fail "A3 agent-owned local file run"
fi

artifacts_json="${TMP_DIR}/artifacts.json"
preview_json="${TMP_DIR}/artifact-preview.json"
if [[ -n "${read_run_id}" && -n "${read_artifact}" ]] \
  && poll_artifact_preview_ready "default" "${read_run_id}" "${read_artifact}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${artifacts_json}" "${preview_json}"; then
  pass "A4 artifact preview"
else
  fail "A4 artifact preview"
fi

browser_payload="$(jq -nc '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test browser text extraction via research agent.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {tool:"browser_automation.interactive", mode:"extract_text", url:"https://example.com"}
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_start_json="${TMP_DIR}/browser-start.json"
api_post "/turn" "${browser_payload}" > "${browser_start_json}"
browser_run_id="$(jq -r '.run_id // empty' "${browser_start_json}")"
browser_run_json="${TMP_DIR}/browser-run.json"
if [[ -n "${browser_run_id}" ]] && poll_run_terminal "${browser_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${browser_run_json}"; then
  browser_status="$(jq -r '.status // empty' "${browser_run_json}")"
  browser_tool="$(jq -r '(.result_data.outputs.actions // [])[0].tool // empty' "${browser_run_json}")"
  browser_artifact_count="$(jq -r '(.result_data.outputs.artifacts // []) | length' "${browser_run_json}")"
  if [[ "${browser_status}" == "completed" && "${browser_tool}" == "browser_automation.interactive" && "${browser_artifact_count}" -ge 2 ]]; then
    pass "A5 browser automation"
  else
    fail "A5 browser automation"
  fi
else
  fail "A5 browser automation"
fi

browser_capture_payload="$(jq -nc '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test browser page capture via research agent.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {tool:"browser_automation.interactive", mode:"capture_page", url:"https://example.com"}
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_capture_start_json="${TMP_DIR}/browser-capture-start.json"
api_post "/turn" "${browser_capture_payload}" > "${browser_capture_start_json}"
browser_capture_run_id="$(jq -r '.run_id // empty' "${browser_capture_start_json}")"
browser_capture_run_json="${TMP_DIR}/browser-capture-run.json"
if [[ -n "${browser_capture_run_id}" ]] && poll_run_terminal "${browser_capture_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${browser_capture_run_json}"; then
  browser_capture_status="$(jq -r '.status // empty' "${browser_capture_run_json}")"
  browser_capture_mode="$(jq -r '(.result_data.outputs.actions // [])[0].mode // empty' "${browser_capture_run_json}")"
  browser_capture_kinds="$(jq -r '(.result_data.outputs.artifacts // []) | map(.kind) | join(",")' "${browser_capture_run_json}")"
  if [[ "${browser_capture_status}" == "completed" && "${browser_capture_mode}" == "capture_page" ]] \
    && grep -q "screenshot" <<<"${browser_capture_kinds}" \
    && grep -q "report" <<<"${browser_capture_kinds}"; then
    pass "A6 browser page capture"
  else
    fail "A6 browser page capture"
  fi
else
  fail "A6 browser page capture"
fi

browser_session_seed_payload="$(jq -nc --arg url "${browser_session_url}?seed=Persisted" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test browser session persistence.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          session_profile:"smoke-browser-profile",
          browser_permissions:{allow:true}
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_session_seed_start_json="${TMP_DIR}/browser-session-seed-start.json"
browser_session_seed_precheck_json="${TMP_DIR}/browser-session-seed-precheck.json"
api_post "/runs/precheck" "${browser_session_seed_payload}" > "${browser_session_seed_precheck_json}"
api_post "/turn" "${browser_session_seed_payload}" > "${browser_session_seed_start_json}"
browser_session_seed_run_id="$(jq -r '.run_id // empty' "${browser_session_seed_start_json}")"
browser_session_seed_approval_id="$(jq -r '.pending_approval.approval_id // empty' "${browser_session_seed_start_json}")"
browser_session_seed_run_json="${TMP_DIR}/browser-session-seed-run.json"
browser_session_check_payload="$(jq -nc --arg url "${browser_session_url}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Verify browser session persistence.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          session_profile:"smoke-browser-profile",
          browser_permissions:{allow:true}
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_session_check_start_json="${TMP_DIR}/browser-session-check-start.json"
browser_session_check_precheck_json="${TMP_DIR}/browser-session-check-precheck.json"
api_post "/runs/precheck" "${browser_session_check_payload}" > "${browser_session_check_precheck_json}"
if [[ -n "${browser_session_seed_run_id}" && -n "${browser_session_seed_approval_id}" ]] \
  && [[ "$(jq -r '.tool_policy_precheck.approval_required_count // 0' "${browser_session_seed_precheck_json}")" == "1" ]] \
  && [[ "$(jq -r '.status // empty' "${browser_session_seed_start_json}")" == "waiting_for_input" ]]; then
  browser_session_seed_resolve_json="${TMP_DIR}/browser-session-seed-resolve.json"
  api_post "/runs/${browser_session_seed_run_id}/approvals/${browser_session_seed_approval_id}/resolve" '{"decision":"proceed","note":"session seed approved"}' > "${browser_session_seed_resolve_json}"
fi
if [[ -n "${browser_session_seed_run_id}" ]] && poll_run_terminal "${browser_session_seed_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${browser_session_seed_run_json}" \
  && [[ "$(jq -r '.status // empty' "${browser_session_seed_run_json}")" == "completed" ]]; then
  api_post "/turn" "${browser_session_check_payload}" > "${browser_session_check_start_json}"
  browser_session_check_run_id="$(jq -r '.run_id // empty' "${browser_session_check_start_json}")"
  browser_session_check_approval_id="$(jq -r '.pending_approval.approval_id // empty' "${browser_session_check_start_json}")"
  browser_session_check_run_json="${TMP_DIR}/browser-session-check-run.json"
  if [[ -n "${browser_session_check_run_id}" && -n "${browser_session_check_approval_id}" ]] \
    && [[ "$(jq -r '.tool_policy_precheck.approval_required_count // 0' "${browser_session_check_precheck_json}")" == "1" ]] \
    && [[ "$(jq -r '.status // empty' "${browser_session_check_start_json}")" == "waiting_for_input" ]]; then
    browser_session_check_resolve_json="${TMP_DIR}/browser-session-check-resolve.json"
    api_post "/runs/${browser_session_check_run_id}/approvals/${browser_session_check_approval_id}/resolve" '{"decision":"proceed","note":"session check approved"}' > "${browser_session_check_resolve_json}"
  fi
  if [[ -n "${browser_session_check_run_id}" ]] && poll_run_terminal "${browser_session_check_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${browser_session_check_run_json}" \
    && [[ "$(jq -r '.status // empty' "${browser_session_check_run_json}")" == "completed" ]] \
    && grep -q "Persisted" <<<"$(jq -r '(.result_data.outputs.actions // [])[0].text_preview // empty' "${browser_session_check_run_json}")"; then
    pass "A7 browser session persistence"
  else
    fail "A7 browser session persistence"
  fi
else
  fail "A7 browser session persistence"
fi

browser_script_payload="$(jq -nc --arg url "${browser_session_url}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test ordered browser script actions.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"wait","selector":"#go"},
            {"action":"type","selector":"#search","text":"Sequence"},
            {"action":"click","selector":"#go"},
            {"action":"sleep","ms":300}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_script_start_json="${TMP_DIR}/browser-script-start.json"
api_post "/turn" "${browser_script_payload}" > "${browser_script_start_json}"
browser_script_run_id="$(jq -r '.run_id // empty' "${browser_script_start_json}")"
browser_script_run_json="${TMP_DIR}/browser-script-run.json"
if [[ -n "${browser_script_run_id}" ]] && poll_run_terminal "${browser_script_run_id}" 120 1 "${browser_script_run_json}" \
  && [[ "$(jq -r '.status // empty' "${browser_script_run_json}")" == "completed" ]] \
  && grep -q "Sequence" <<<"$(jq -r '(.result_data.outputs.actions // [])[0].text_preview // empty' "${browser_script_run_json}")"; then
  pass "A8 browser action script"
else
  fail "A8 browser action script"
fi

mkdir -p temp_executions
browser_upload_file="temp_executions/browser-upload-smoke.txt"
printf 'Uploaded via Empyralis smoke' > "${browser_upload_file}"
browser_upload_payload="$(jq -nc --arg url "${browser_session_url}" --arg upload_path "${browser_upload_file}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test browser upload and extract actions.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"wait","selector":"#upload"},
            {"action":"select","selector":"#choice","value":"beta"},
            {"action":"upload","selector":"#upload","path":$upload_path},
            {"action":"sleep","ms":400},
            {"action":"extract","selector":"#upload-state"},
            {"action":"extract","selector":"#choice"}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_upload_start_json="${TMP_DIR}/browser-upload-start.json"
api_post "/turn" "${browser_upload_payload}" > "${browser_upload_start_json}"
browser_upload_run_id="$(jq -r '.run_id // empty' "${browser_upload_start_json}")"
browser_upload_run_json="${TMP_DIR}/browser-upload-run.json"
if [[ -n "${browser_upload_run_id}" ]] && poll_run_terminal "${browser_upload_run_id}" 120 1 "${browser_upload_run_json}" \
  && [[ "$(jq -r '.status // empty' "${browser_upload_run_json}")" == "completed" ]] \
  && jq -e '
    ((.result_data.outputs.actions // [])[0].action_results // []) as $results
    | ([$results[] | .text // empty] | join("\n") | contains("Uploaded via Empyralis smoke"))
      and
      ([$results[] | .value // empty] | index("beta") != null)
  ' "${browser_upload_run_json}" >/dev/null; then
  pass "A9 browser upload and extract"
else
  fail "A9 browser upload and extract"
fi

approval_payload="$(jq -nc --arg url "${browser_session_url}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test approval-gated browser session run.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          session_profile:"smoke-browser-profile",
          browser_permissions:{allow:true}
        }
      ],
      continue_on_error:false
    }
  }
}')"
precheck_json="${TMP_DIR}/approval-precheck.json"
api_post "/runs/precheck" "${approval_payload}" > "${precheck_json}"
approval_start_json="${TMP_DIR}/approval-start.json"
api_post "/turn" "${approval_payload}" > "${approval_start_json}"
approval_run_id="$(jq -r '.run_id // empty' "${approval_start_json}")"
approval_id="$(jq -r '.pending_approval.approval_id // empty' "${approval_start_json}")"
if [[ -n "${approval_run_id}" && -n "${approval_id}" ]] \
  && [[ "$(jq -r '.tool_policy_precheck.approval_required_count // 0' "${precheck_json}")" == "1" ]] \
  && [[ "$(jq -r '.status // empty' "${approval_start_json}")" == "waiting_for_input" ]]; then
  resolve_json="${TMP_DIR}/approval-resolve.json"
  api_post "/runs/${approval_run_id}/approvals/${approval_id}/resolve" '{"decision":"proceed","note":"smoke approved"}' > "${resolve_json}"
  approval_run_json="${TMP_DIR}/approval-run.json"
  if poll_run_terminal "${approval_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${approval_run_json}" \
    && [[ "$(jq -r '.status // empty' "${approval_run_json}")" == "completed" ]]; then
    pass "A10 approval-gated browser session run"
  else
    fail "A10 approval-gated browser session run"
  fi
else
  fail "A10 approval-gated browser session run"
fi

audit_json="${TMP_DIR}/audit.json"
if api_get "/approvals/audit?limit=60" > "${audit_json}" \
  && jq -e --arg rid "${approval_run_id}" '.items | map(select(.run_id == $rid)) | length > 0' "${audit_json}" >/dev/null; then
  pass "A11 approval audit trail"
else
  fail "A11 approval audit trail"
fi

agent_json="${TMP_DIR}/agent-detail.json"
if api_get "/agents/workspace/agents/private-assistant?workspace_id=default&history_limit=8&file_limit=8&artifact_limit=8" > "${agent_json}" \
  && jq -e --arg rid "${read_run_id}" '((.history // []) | map(select(.run_id == $rid)) | length > 0) or (.overview.last_completed_run_id == $rid)' "${agent_json}" >/dev/null; then
  pass "A12 agent detail hydration"
else
  fail "A12 agent detail hydration"
fi

delegate_parent_payload="$(jq -nc '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Coordinate a short delegation smoke test.",
  agent_role:"orchestrator",
  metadata:{
    execution_target:"local_companion",
    trust_mode:"guarded"
  }
}')"
delegate_parent_start_json="${TMP_DIR}/delegate-parent-start.json"
api_post "/turn" "${delegate_parent_payload}" > "${delegate_parent_start_json}"
delegate_parent_run_id="$(jq -r '.run_id // empty' "${delegate_parent_start_json}")"
if [[ -n "${delegate_parent_run_id}" ]]; then
  delegate_payload="$(jq -nc '{
    note:"smoke delegation",
    children:[
      {
        agent_role:"research",
        user_goal:"Create three short market insights for the smoke test."
      }
    ]
  }')"
  delegate_result_json="${TMP_DIR}/delegate-result.json"
  api_post "/runs/${delegate_parent_run_id}/delegate" "${delegate_payload}" > "${delegate_result_json}"
  delegate_child_run_id="$(jq -r '.items[0].run_id // empty' "${delegate_result_json}")"
  if [[ -n "${delegate_child_run_id}" ]]; then
    parent_detail_json="${TMP_DIR}/delegate-parent-detail.json"
    child_detail_json="${TMP_DIR}/delegate-child-detail.json"
    sleep 1
    api_get "/runs/${delegate_parent_run_id}" > "${parent_detail_json}"
    api_get "/runs/${delegate_child_run_id}" > "${child_detail_json}"
    if jq -e --arg rid "${delegate_child_run_id}" '.child_runs | map(select(.run_id == $rid)) | length > 0' "${parent_detail_json}" >/dev/null \
      && [[ "$(jq -r '.parent_run_id // empty' "${child_detail_json}")" == "${delegate_parent_run_id}" ]] \
      && [[ "$(jq -r '.delegated_by_role // empty' "${child_detail_json}")" == "orchestrator" ]]; then
      pass "A13 orchestrator delegation"
    else
      fail "A13 orchestrator delegation"
    fi
  else
    fail "A13 orchestrator delegation"
  fi
else
  fail "A13 orchestrator delegation"
fi

auto_delegate_parent_payload="$(jq -nc '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Research the market and implement a platform improvement for the smoke test.",
  agent_role:"orchestrator",
  metadata:{
    execution_target:"local_companion",
    trust_mode:"guarded"
  }
}')"
auto_delegate_parent_start_json="${TMP_DIR}/auto-delegate-parent-start.json"
api_post "/turn" "${auto_delegate_parent_payload}" > "${auto_delegate_parent_start_json}"
auto_delegate_parent_run_id="$(jq -r '.run_id // empty' "${auto_delegate_parent_start_json}")"
if [[ -n "${auto_delegate_parent_run_id}" ]]; then
  auto_delegate_result_json="${TMP_DIR}/auto-delegate-result.json"
  api_post "/runs/${auto_delegate_parent_run_id}/delegate/auto" '{"note":"smoke auto delegation","max_children":3}' > "${auto_delegate_result_json}"
  auto_delegate_parent_detail_json="${TMP_DIR}/auto-delegate-parent-detail.json"
  sleep 1
  api_get "/runs/${auto_delegate_parent_run_id}" > "${auto_delegate_parent_detail_json}"
  if jq -e '
    (.items | length >= 2)
    and ((.items | map(.agent_role) | index("research")) != null)
    and ((.items | map(.agent_role) | index("builder")) != null)
  ' "${auto_delegate_result_json}" >/dev/null \
    && jq -e '
      (.child_runs | map(.agent_role) | index("research")) != null
      and
      (.child_runs | map(.agent_role) | index("builder")) != null
    ' "${auto_delegate_parent_detail_json}" >/dev/null; then
    pass "A19 orchestrator auto-delegation"
  else
    fail "A19 orchestrator auto-delegation"
  fi
else
  fail "A19 orchestrator auto-delegation"
fi

if [[ -n "${auto_delegate_parent_run_id:-}" ]]; then
  a20_summary_jq="$(read_jq_program <<'JQ'
((.delegation_summary // .result_data.orchestration.summary // {}) as $summary
| ($summary.ready == true)
and ((($summary.total_children // 0) >= 2) or (($summary.effective_children // 0) >= 2))
and (($summary.child_roles // []) | index("research") != null)
and (($summary.child_roles // []) | index("builder") != null)
and (($summary.summary_text // "") | length > 0))
JQ
)"
  auto_delegate_child_ids="$(jq -r '.items[].run_id // empty' "${auto_delegate_result_json}" 2>/dev/null || true)"
  auto_children_ready=1
  while IFS= read -r child_id; do
    [[ -z "${child_id}" ]] && continue
    child_json="${TMP_DIR}/auto-child-${child_id}.json"
    if ! poll_run_terminal "${child_id}" "${ORCHESTRATOR_ATTEMPTS}" 1 "${child_json}"; then
      auto_children_ready=0
      break
    fi
  done <<< "${auto_delegate_child_ids}"
  auto_delegate_summary_json="${TMP_DIR}/auto-delegate-summary.json"
  if [[ "${auto_children_ready}" == "1" ]] \
    && poll_run_snapshot_condition "${auto_delegate_parent_run_id}" "${ORCHESTRATOR_ATTEMPTS}" 1 "${a20_summary_jq}" "${auto_delegate_summary_json}"; then
    pass "A20 orchestration summary merge"
  else
    fail "A20 orchestration summary merge"
  fi
else
  fail "A20 orchestration summary merge"
fi

retry_delegate_parent_payload="$(jq -nc '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Coordinate a retry-path delegation smoke test.",
  agent_role:"orchestrator",
  metadata:{
    execution_target:"local_companion",
    trust_mode:"guarded"
  }
}')"
retry_delegate_parent_start_json="${TMP_DIR}/retry-delegate-parent-start.json"
api_post "/turn" "${retry_delegate_parent_payload}" > "${retry_delegate_parent_start_json}"
retry_delegate_parent_run_id="$(jq -r '.run_id // empty' "${retry_delegate_parent_start_json}")"
if [[ -n "${retry_delegate_parent_run_id}" ]]; then
  a21_summary_jq="$(read_jq_program <<'JQ'
((.delegation_summary // .result_data.orchestration.summary // {}) as $summary
| ($summary.failed_children // 0) >= 1
and ($summary.retryable_failed_children // 0) >= 1
and ($summary.next_action == "retry_failed_children")
and (($summary.failed_run_ids // []) | length >= 1))
JQ
)"
  a22_retryable_jq="$(read_jq_program <<'JQ'
((.delegation_summary // .result_data.orchestration.summary // {}) as $summary
| ($summary.retryable_failed_children // 0) >= 1
and (($summary.failed_run_ids // []) | length >= 1))
JQ
)"
  retry_delegate_payload="$(jq -nc '{
    note:"smoke failed child delegation",
    children:[
      {
        agent_role:"builder",
        user_goal:"Fail on purpose by reading a missing file.",
        metadata:{
          outcome_pack:"local-execution-v1",
          execution_target:"local_companion",
          trust_mode:"guarded",
          pack_inputs:{
            operations:[
              {
                tool:"filesystem.read_write",
                mode:"read",
                path:"tmp/empyralis-smoke-missing-file.txt"
              }
            ],
            continue_on_error:false
          }
        }
      }
    ]
  }')"
  retry_delegate_result_json="${TMP_DIR}/retry-delegate-result.json"
  api_post "/runs/${retry_delegate_parent_run_id}/delegate" "${retry_delegate_payload}" > "${retry_delegate_result_json}"
  retry_failed_child_run_id="$(jq -r '.items[0].run_id // empty' "${retry_delegate_result_json}")"
  retry_failed_child_json="${TMP_DIR}/retry-failed-child.json"
  retry_parent_summary_json="${TMP_DIR}/retry-parent-summary.json"
  if [[ -n "${retry_failed_child_run_id}" ]] \
    && poll_run_terminal "${retry_failed_child_run_id}" "${ORCHESTRATOR_ATTEMPTS}" 1 "${retry_failed_child_json}" \
    && poll_run_snapshot_condition "${retry_delegate_parent_run_id}" "${ORCHESTRATOR_ATTEMPTS}" 1 "${a21_summary_jq}" "${retry_parent_summary_json}"; then
    pass "A21 orchestrator failed-child summary"
  else
    fail "A21 orchestrator failed-child summary"
  fi

  retry_delegate_retry_json="${TMP_DIR}/retry-delegate-retry.json"
  if poll_run_snapshot_condition "${retry_delegate_parent_run_id}" "${ORCHESTRATOR_ATTEMPTS}" 1 "${a22_retryable_jq}" "${retry_parent_summary_json}" \
    && api_post "/runs/${retry_delegate_parent_run_id}/delegate/retry-failed" '{"note":"smoke retry failed child"}' > "${retry_delegate_retry_json}" \
    && jq -e --arg rid "${retry_failed_child_run_id}" '
      (.count // 0) >= 1
      and (.items | length >= 1)
      and (.items[0].retry_of_run_id == $rid)
      and ((.items[0].retry_sequence // 0) >= 1)
    ' "${retry_delegate_retry_json}" >/dev/null; then
    pass "A22 orchestrator retry failed child"
  else
    fail "A22 orchestrator retry failed child"
  fi
else
  fail "A21 orchestrator failed-child summary"
  fail "A22 orchestrator retry failed child"
fi

browser_iframe_payload="$(jq -nc --arg url "${browser_session_url}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test same-origin iframe targeting.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"wait","frame":"#panel","selector":"#frame-go"},
            {"action":"type","frame":"#panel","selector":"#frame-search","text":"Inside frame"},
            {"action":"click","frame":"#panel","selector":"#frame-go"},
            {"action":"extract","frame":"#panel","selector":"#frame-result"}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_iframe_start_json="${TMP_DIR}/browser-iframe-start.json"
api_post "/turn" "${browser_iframe_payload}" > "${browser_iframe_start_json}"
browser_iframe_run_id="$(jq -r '.run_id // empty' "${browser_iframe_start_json}")"
browser_iframe_run_json="${TMP_DIR}/browser-iframe-run.json"
if [[ -n "${browser_iframe_run_id}" ]] && poll_run_terminal "${browser_iframe_run_id}" 120 1 "${browser_iframe_run_json}" \
  && poll_run_snapshot_condition "${browser_iframe_run_id}" 60 1 '
    (.status == "completed")
    and (((.result_data.outputs.actions // []) | map(.action_results // []) | add // []) as $results
      | ([$results[] | select((.frame // "") == "#panel") | .text // empty] | join("\n") | contains("Inside frame")))
  ' "${browser_iframe_run_json}"; then
  pass "A14 browser iframe targeting"
else
  fail "A14 browser iframe targeting"
fi

browser_tab_payload="$(jq -nc --arg url "${browser_session_url}" --arg second_url "http://127.0.0.1:${browser_session_port_value}/second.html" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test browser multi-tab flow.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"open_tab","tab":"secondary","url":$second_url},
            {"action":"wait","tab":"secondary","selector":"#second-ready"},
            {"action":"extract","tab":"secondary","selector":"#second-ready"},
            {"action":"switch_tab","tab":"main"},
            {"action":"extract","tab":"main","selector":"#ready"}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_tab_start_json="${TMP_DIR}/browser-tab-start.json"
api_post "/turn" "${browser_tab_payload}" > "${browser_tab_start_json}"
browser_tab_run_id="$(jq -r '.run_id // empty' "${browser_tab_start_json}")"
browser_tab_run_json="${TMP_DIR}/browser-tab-run.json"
if [[ -n "${browser_tab_run_id}" ]] && poll_run_terminal "${browser_tab_run_id}" 120 1 "${browser_tab_run_json}" \
  && poll_run_snapshot_condition "${browser_tab_run_id}" 60 1 '
    (.status == "completed")
    and (((.result_data.outputs.actions // []) | map(.action_results // []) | add // []) as $results
      | ([$results[] | select((.tab // "") == "secondary") | .text // empty] | join("\n") | contains("Second page ready"))
      and
      ([$results[] | select((.tab // "") == "main") | .text // empty] | join("\n") | contains("Browser Session Smoke")))
  ' "${browser_tab_run_json}"; then
  pass "A15 browser tab flow"
else
  fail "A15 browser tab flow"
fi

browser_download_payload="$(jq -nc --arg url "${browser_session_url}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test browser download handoff.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"wait","selector":"#download-link"},
            {"action":"download","selector":"#download-link","ms":8000}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_download_start_json="${TMP_DIR}/browser-download-start.json"
api_post "/turn" "${browser_download_payload}" > "${browser_download_start_json}"
browser_download_run_id="$(jq -r '.run_id // empty' "${browser_download_start_json}")"
browser_download_run_json="${TMP_DIR}/browser-download-run.json"
if [[ -n "${browser_download_run_id}" ]] && poll_run_terminal "${browser_download_run_id}" 120 1 "${browser_download_run_json}" \
  && [[ "$(jq -r '.status // empty' "${browser_download_run_json}")" == "completed" ]] \
  && jq -e '
    ((.result_data.outputs.actions // [])[0] // {}) as $action
    | (($action.downloads // []) | length > 0)
      and ((($action.downloads // [])[0].filename // "") | tostring | contains("download"))
      and ((.result_data.outputs.artifacts // []) | map(select(.kind == "download")) | length > 0)
  ' "${browser_download_run_json}" >/dev/null; then
  pass "A16 browser download handoff"
else
  fail "A16 browser download handoff"
fi

browser_popup_payload="$(jq -nc --arg url "${browser_session_url}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test browser popup flow.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"wait","selector":"#popup-link"},
            {"action":"open_popup","selector":"#popup-link","tab":"popup","ms":10000},
            {"action":"wait","tab":"popup","selector":"#popup-ready"},
            {"action":"extract","tab":"popup","selector":"#popup-copy"}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_popup_start_json="${TMP_DIR}/browser-popup-start.json"
api_post "/turn" "${browser_popup_payload}" > "${browser_popup_start_json}"
browser_popup_run_id="$(jq -r '.run_id // empty' "${browser_popup_start_json}")"
browser_popup_run_json="${TMP_DIR}/browser-popup-run.json"
if [[ -n "${browser_popup_run_id}" ]] && poll_run_terminal "${browser_popup_run_id}" 120 1 "${browser_popup_run_json}" \
  && [[ "$(jq -r '.status // empty' "${browser_popup_run_json}")" == "completed" ]] \
  && jq -e '
    ((.result_data.outputs.actions // [])[0].action_results // []) as $results
    | ([$results[] | select((.action // "") == "open_popup") | .tab // empty] | index("popup") != null)
      and
      ([$results[] | select((.tab // "") == "popup") | .text // empty] | join("\n") | contains("Popup smoke target"))
  ' "${browser_popup_run_json}" >/dev/null; then
  pass "A17 browser popup flow"
else
  fail "A17 browser popup flow"
fi

browser_auth_payload="$(jq -nc --arg url "${browser_session_url}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test authenticated interactive browser approval.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          session_profile:"smoke-auth-browser",
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"wait","selector":"#go"},
            {"action":"type","selector":"#search","text":"Auth smoke"},
            {"action":"click","selector":"#go"},
            {"action":"sleep","ms":300},
            {"action":"extract","selector":"#state"}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_auth_precheck_json="${TMP_DIR}/browser-auth-precheck.json"
api_post "/runs/precheck" "${browser_auth_payload}" > "${browser_auth_precheck_json}"
browser_auth_start_json="${TMP_DIR}/browser-auth-start.json"
api_post "/turn" "${browser_auth_payload}" > "${browser_auth_start_json}"
browser_auth_run_id="$(jq -r '.run_id // empty' "${browser_auth_start_json}")"
browser_auth_approval_id="$(jq -r '.pending_approval.approval_id // empty' "${browser_auth_start_json}")"
browser_auth_run_json="${TMP_DIR}/browser-auth-run.json"
if [[ -n "${browser_auth_run_id}" && -n "${browser_auth_approval_id}" ]] \
  && [[ "$(jq -r '.tool_policy_precheck.approval_required_count // 0' "${browser_auth_precheck_json}")" == "1" ]] \
  && jq -e '.tool_policy_precheck.items[0].reason == "browser_authenticated_interactive_requires_reviewed_approval"' "${browser_auth_precheck_json}" >/dev/null \
  && [[ "$(jq -r '.status // empty' "${browser_auth_start_json}")" == "waiting_for_input" ]]; then
  api_post "/runs/${browser_auth_run_id}/approvals/${browser_auth_approval_id}/resolve" '{"decision":"proceed","note":"interactive browser approved"}' >/dev/null
  if poll_run_terminal "${browser_auth_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${browser_auth_run_json}" \
    && [[ "$(jq -r '.status // empty' "${browser_auth_run_json}")" == "completed" ]]; then
    pass "A18 authenticated browser interactive reviewed approval"
  else
    fail "A18 authenticated browser interactive reviewed approval"
  fi
else
  fail "A18 authenticated browser interactive reviewed approval"
fi

browser_auth_priv_payload="$(jq -nc --arg url "${browser_session_url}" --arg upload_path "${browser_upload_file}" '{
  engine:"orion",
  workspace_id:"default",
  user_goal:"Smoke test authenticated privileged browser approval.",
  agent_role:"research",
  metadata:{
    outcome_pack:"local-execution-v1",
    execution_target:"local_companion",
    trust_mode:"guarded",
    pack_inputs:{
      operations:[
        {
          tool:"browser_automation.interactive",
          mode:"capture_page",
          url:$url,
          session_profile:"smoke-browser-profile",
          browser_permissions:{allow:true},
          browser_actions:[
            {"action":"wait","selector":"#upload"},
            {"action":"upload","selector":"#upload","path":$upload_path},
            {"action":"extract","selector":"#upload-state"}
          ]
        }
      ],
      continue_on_error:false
    }
  }
}')"
ensure_browser_session_server
browser_auth_priv_precheck_json="${TMP_DIR}/browser-auth-priv-precheck.json"
api_post "/runs/precheck" "${browser_auth_priv_payload}" > "${browser_auth_priv_precheck_json}"
browser_auth_priv_start_json="${TMP_DIR}/browser-auth-priv-start.json"
api_post "/turn" "${browser_auth_priv_payload}" > "${browser_auth_priv_start_json}"
browser_auth_priv_run_id="$(jq -r '.run_id // empty' "${browser_auth_priv_start_json}")"
browser_auth_priv_approval_id="$(jq -r '.pending_approval.approval_id // empty' "${browser_auth_priv_start_json}")"
browser_auth_priv_run_json="${TMP_DIR}/browser-auth-priv-run.json"
if [[ -n "${browser_auth_priv_run_id}" && -n "${browser_auth_priv_approval_id}" ]] \
  && [[ "$(jq -r '.tool_policy_precheck.approval_required_count // 0' "${browser_auth_priv_precheck_json}")" == "1" ]] \
  && [[ "$(jq -r '.tool_policy_precheck.browser_automation_policy.profile // empty' "${browser_auth_priv_precheck_json}")" == "authenticated_privileged" ]] \
  && jq -e '((.tool_policy_precheck.browser_automation_policy.privileged_actions // []) | index("upload")) != null' "${browser_auth_priv_precheck_json}" >/dev/null \
  && jq -e '.tool_policy_precheck.items[0].reason == "browser_authenticated_privileged_requires_reviewed_approval"' "${browser_auth_priv_precheck_json}" >/dev/null \
  && [[ "$(jq -r '.status // empty' "${browser_auth_priv_start_json}")" == "waiting_for_input" ]]; then
  api_post "/runs/${browser_auth_priv_run_id}/approvals/${browser_auth_priv_approval_id}/resolve" '{"decision":"proceed","note":"privileged browser approved"}' >/dev/null
  if poll_run_terminal "${browser_auth_priv_run_id}" "${SMOKE_POLL_ATTEMPTS}" "${SMOKE_POLL_SLEEP}" "${browser_auth_priv_run_json}" \
    && [[ "$(jq -r '.status // empty' "${browser_auth_priv_run_json}")" == "completed" ]]; then
    pass "A23 authenticated privileged browser reviewed approval"
  else
    fail "A23 authenticated privileged browser reviewed approval"
  fi
else
  fail "A23 authenticated privileged browser reviewed approval"
fi

workflow_smoke_json="${TMP_DIR}/workflow-smoke.json"
if RUNTIME_KEY="${RUNTIME_KEY}" BACKEND_API_URL="${EMPYRALIS_BACKEND_API_URL:-http://127.0.0.1:4000/api/v1}" WORKFLOW_SMOKE_JSON="${workflow_smoke_json}" SMOKE_POLL_ATTEMPTS="${SMOKE_POLL_ATTEMPTS}" SMOKE_POLL_SLEEP="${SMOKE_POLL_SLEEP}" python3 - <<'PY'
import json
import os
import time
import urllib.request

runtime_key = os.environ["RUNTIME_KEY"]
base = os.environ["BACKEND_API_URL"].rstrip("/")
out_file = os.environ["WORKFLOW_SMOKE_JSON"]
poll_attempts = int(os.environ.get("SMOKE_POLL_ATTEMPTS", "60"))
poll_sleep = float(os.environ.get("SMOKE_POLL_SLEEP", "5"))
headers = {"X-API-Key": runtime_key, "Content-Type": "application/json"}

def req(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    url = path if path.startswith("http://") or path.startswith("https://") else f"{base}{path}"
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}

workflow_payload = {
    "name": "Workflow Smoke Live",
    "description": "Live create/publish/run smoke",
    "definition": {
        "version": "empyralist.workflow.v2",
        "nodes": [
            {
                "id": "trigger_1",
                "type": "trigger",
                "variant": "connector_event",
                "data": {"label": "Inbound"},
                "config": {"connector": "telegram_bot", "event": "inbound_message"},
            },
            {
                "id": "data_1",
                "type": "data",
                "variant": "transform",
                "data": {"label": "Transform"},
                "config": {"template": "Normalize the inbound event"},
            },
        ],
        "edges": [{"id": "edge_1", "source": "trigger_1", "target": "data_1"}],
    },
}

status, created = req("POST", "/workflows?workspaceId=default", workflow_payload)
workflow_id = created["id"]
status, fetched = req("GET", f"/workflows/{workflow_id}")
status, published = req("POST", f"/workflows/{workflow_id}/publish")
runtime_base = os.environ.get("EMPYRALIS_API_URL") or os.environ.get("ORION_API_URL") or "http://127.0.0.1:8001"
status, execution = req("POST", f"{runtime_base.rstrip('/')}/turn", {
    "engine": "orion",
    "workflow_id": workflow_id,
    "workspace_id": "default",
    "user_goal": "Smoke run for workflow plumbing",
    "metadata": {
        "origin": "workflow_runtime_smoke",
        "trust_mode": "guarded",
    },
})
execution_id = execution["run_id"]
final = None
for _ in range(poll_attempts):
    status, final = req("GET", f"{runtime_base.rstrip('/')}/runs/{execution_id}")
    if final.get("status") in {"completed", "waiting_for_input", "failed"}:
        break
    time.sleep(poll_sleep)

payload = {
    "workflow_id": workflow_id,
    "publish_status": published.get("status"),
    "execution_id": execution_id,
    "execution_status": final.get("status") if isinstance(final, dict) else None,
    "workflow_validation": fetched.get("validation", {}) if isinstance(fetched, dict) else {},
}
with open(out_file, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)

if payload["publish_status"] != "published" or payload["execution_status"] != "completed":
    raise SystemExit(1)
PY
then
  pass "A24 workflow create publish execute via runtime"
else
  cat "${workflow_smoke_json}" 2>/dev/null || true
  fail "A24 workflow create publish execute via runtime"
fi

if [[ "${SCREENSHOT_SMOKE}" == "1" ]]; then
  screenshot_payload="$(jq -nc '{
    engine:"orion",
    workspace_id:"default",
    user_goal:"Smoke screenshot capture.",
    agent_role:"builder",
    metadata:{
      outcome_pack:"local-execution-v1",
      execution_target:"local_companion",
      trust_mode:"guarded",
      pack_inputs:{
        operations:[
          {tool:"screenshot.capture"}
        ],
        continue_on_error:false
      }
    }
  }')"
  screenshot_start_json="${TMP_DIR}/screenshot-start.json"
  api_post "/turn" "${screenshot_payload}" > "${screenshot_start_json}"
  screenshot_run_id="$(jq -r '.run_id // empty' "${screenshot_start_json}")"
  screenshot_run_json="${TMP_DIR}/screenshot-run.json"
  if [[ -n "${screenshot_run_id}" ]] && poll_run_terminal "${screenshot_run_id}" 90 1 "${screenshot_run_json}" && [[ "$(jq -r '.status // empty' "${screenshot_run_json}")" == "completed" ]]; then
    pass "M3 screenshot capture"
  else
    fail "M3 screenshot capture"
  fi
fi

echo
printf 'Summary: %s passed, %s failed\n' "${PASSES}" "${FAILURES}"
mkdir -p "${VALIDATION_REPORT_DIR}"
VALIDATION_REPORT_DIR="${VALIDATION_REPORT_DIR}" \
VALIDATION_REPORT_FILE="${VALIDATION_REPORT_FILE}" \
VALIDATION_CHECKS_FILE="${VALIDATION_CHECKS_FILE}" \
VALIDATION_PASSES="${PASSES}" \
VALIDATION_FAILURES="${FAILURES}" \
VALIDATION_API_URL="${API_URL}" \
python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

report_dir = Path(os.environ["VALIDATION_REPORT_DIR"])
report_file = Path(os.environ["VALIDATION_REPORT_FILE"])
checks_file = Path(os.environ["VALIDATION_CHECKS_FILE"])
passes = int(os.environ["VALIDATION_PASSES"])
failures = int(os.environ["VALIDATION_FAILURES"])
api_url = os.environ["VALIDATION_API_URL"]

checks = []
if checks_file.exists():
    for raw_line in checks_file.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        status, name = raw_line.split("\t", 1)
        checks.append({"name": name, "status": status})

generated_at = datetime.now(timezone.utc).isoformat()
payload = {
    "suite": "empyralis_core_smoke",
    "generated_at": generated_at,
    "api_url": api_url,
    "summary": {
        "pass": passes,
        "fail": failures,
        "total": passes + failures,
        "ok": failures == 0,
    },
    "checks": checks,
}
report_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
timestamped = report_dir / f"core_smoke_{generated_at.replace(':', '').replace('+00:00', 'Z')}.json"
timestamped.write_text(json.dumps(payload, indent=2), encoding="utf-8")
PY
if [[ "${FAILURES}" -gt 0 ]]; then
  exit 1
fi
