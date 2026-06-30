#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

START_TS="$(date +%s)"

declare -a REPORT_LINES=()
FAIL_COUNT=0

PYTHON_BIN=""
if [[ -x "venv/bin/python" ]]; then
  PYTHON_BIN="venv/bin/python"
elif [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

NOW_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
STATUS_LOG_DIR="${ROOT_DIR}/.tmp/cert"
mkdir -p "${STATUS_LOG_DIR}"

trim_ws() {
  sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//'
}

escape_field() {
  printf '%s' "${1-}" | tr '|' '/' | tr '\n' ' ' | trim_ws
}

record_result() {
  local status="$1"
  local name="$2"
  local command="$3"
  local duration_s="$4"
  local reason="$5"
  REPORT_LINES+=("${status}|${name}|${command}|${duration_s}|${reason}")
}

run_step() {
  local name="$1"
  local command="$2"
  local logfile="${STATUS_LOG_DIR}/${name}.log"

  local step_start
  step_start="$(date +%s)"

  set +e
  bash -lc "${command}" >"${logfile}" 2>&1
  local exit_code=$?
  set -e

  local step_end
  step_end="$(date +%s)"
  local duration_s=$((step_end - step_start))

  if [[ ${exit_code} -eq 0 ]]; then
    record_result "PASS" "${name}" "${command}" "${duration_s}" "ok"
    return 0
  fi

  FAIL_COUNT=$((FAIL_COUNT + 1))
  local reason
  reason="$(tail -n 3 "${logfile}" 2>/dev/null | escape_field)"
  if [[ -z "${reason}" ]]; then
    reason="command failed"
  fi
  record_result "FAIL" "${name}" "${command}" "${duration_s}" "${reason}"
  return 1
}

skip_step() {
  local name="$1"
  local command="$2"
  local reason="$3"
  record_result "SKIPPED" "${name}" "${command}" "0" "$(escape_field "${reason}")"
}

mark_required_skip() {
  local name="$1"
  local command="$2"
  local reason="$3"
  skip_step "${name}" "${command}" "${reason}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

existing_paths() {
  local -a out=()
  local candidate=""
  for candidate in "$@"; do
    if [[ -f "${candidate}" ]]; then
      out+=("${candidate}")
    fi
  done
  if [[ ${#out[@]} -gt 0 ]]; then
    printf '%s\n' "${out[@]}"
  fi
}

changed_file_summary() {
  local status_snapshot staged modified untracked
  status_snapshot="$(git status --short --untracked-files=all)"
  staged="$(printf '%s\n' "${status_snapshot}" | awk 'substr($0,1,1)!=" " && substr($0,1,1)!="?" && NF>0 {c++} END{print c+0}')"
  modified="$(printf '%s\n' "${status_snapshot}" | awk 'substr($0,2,1)!=" " && substr($0,1,1)!="?" && NF>0 {c++} END{print c+0}')"
  untracked="$(printf '%s\n' "${status_snapshot}" | awk 'substr($0,1,2)=="??" {c++} END{print c+0}')"

  printf 'SUMMARY|changed_files|staged=%s|modified=%s|untracked=%s\n' "${staged}" "${modified}" "${untracked}"
  printf 'DETAIL|changed_files|staged_paths=%s\n' "$(escape_field "$(git diff --cached --name-only | head -n 20)")"
  printf 'DETAIL|changed_files|modified_paths=%s\n' "$(escape_field "$(git diff --name-only | head -n 20)")"
  printf 'DETAIL|changed_files|untracked_paths=%s\n' "$(escape_field "$(git ls-files --others --exclude-standard | head -n 20)")"
}

backend_tests() {
  local -a test_candidates=(
    "server_modules/tests/test_auth.py"
    "server_modules/tests/test_auth_account_shell.py"
    "server_modules/tests/test_workspace_bootstrap_service.py"
    "server_modules/tests/test_direct_chat_entry_service.py"
  )
  local tests=()
  tests=($(existing_paths "${test_candidates[@]}"))
  local command_template="${PYTHON_BIN} -m pytest ${tests[*]} -q"

  if [[ -z "${PYTHON_BIN}" ]]; then
    mark_required_skip "backend_tests" "${command_template}" "python interpreter missing"
    return
  fi
  if [[ ${#tests[@]} -eq 0 ]]; then
    mark_required_skip "backend_tests" "${command_template}" "required backend test files missing"
    return
  fi
  run_step "backend_tests" "${command_template}" || true
}

frontend_typecheck() {
  local command="cd frontend && npm run typecheck --silent"
  if [[ ! -d "frontend/node_modules" ]]; then
    mark_required_skip "frontend_typecheck" "${command}" "frontend/node_modules missing (run npm ci in frontend)"
    return
  fi
  run_step "frontend_typecheck" "${command}" || true
}

mobile_typecheck() {
  local command="cd mobile && npx tsc --noEmit"
  if [[ ! -d "mobile/node_modules" ]]; then
    mark_required_skip "mobile_typecheck" "${command}" "mobile/node_modules missing (run npm ci in mobile)"
    return
  fi
  run_step "mobile_typecheck" "${command}" || true
}

gateway_checks() {
  local typecheck_command="cd empyralis-gateway && npm run typecheck --silent"
  local tests_command="cd empyralis-gateway && npm run test --silent"
  if [[ ! -d "empyralis-gateway/node_modules" ]]; then
    mark_required_skip "gateway_typecheck" "${typecheck_command}" "empyralis-gateway/node_modules missing (run npm ci in empyralis-gateway)"
    mark_required_skip "gateway_tests" "${tests_command}" "empyralis-gateway/node_modules missing (run npm ci in empyralis-gateway)"
    return
  fi
  run_step "gateway_typecheck" "${typecheck_command}" || true
  run_step "gateway_tests" "${tests_command}" || true
}

security_tests() {
  local -a test_candidates=(
    "server_modules/tests/test_skill_scanner.py"
    "server_modules/tests/test_tool_broker_guard_service.py"
    "server_modules/tests/test_sage_memory_red_stripping.py"
  )
  local tests=()
  tests=($(existing_paths "${test_candidates[@]}"))
  local command_template="${PYTHON_BIN} -m pytest ${tests[*]} -q"

  if [[ -z "${PYTHON_BIN}" ]]; then
    mark_required_skip "security_tests" "${command_template}" "python interpreter missing"
    return
  fi
  if [[ ${#tests[@]} -eq 0 ]]; then
    mark_required_skip "security_tests" "${command_template}" "required security test files missing"
    return
  fi
  run_step "security_tests" "${command_template}" || true
}

git_checks() {
  run_step "git_diff_check" "git diff --check" || true
}

backend_tests
frontend_typecheck
mobile_typecheck
gateway_checks
security_tests
git_checks

END_TS="$(date +%s)"
TOTAL_S=$((END_TS - START_TS))
RESULT="PASS"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
  RESULT="FAIL"
fi

printf 'HARNESS|local_certification|version=2|generated_at=%s\n' "${NOW_UTC}"
printf 'STATUS|STEP|COMMAND|DURATION_SECONDS|REASON\n'
for line in "${REPORT_LINES[@]}"; do
  printf '%s\n' "${line}"
done
changed_file_summary
printf 'SUMMARY|local_certification|total_duration_seconds=%s|result=%s|failed_steps=%s|logs_dir=%s\n' "${TOTAL_S}" "${RESULT}" "${FAIL_COUNT}" "${STATUS_LOG_DIR}"

if [[ "${RESULT}" != "PASS" ]]; then
  printf 'ACTION|required|review failed steps in %s and rerun this harness before push\n' "${STATUS_LOG_DIR}"
  exit 1
fi

printf 'ACTION|ready|repo is locally push-ready based on this harness scope\n'
exit 0
