#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

PASS_COUNT=0
FAIL_COUNT=0

check_file() {
  local path="$1"
  if [[ -f "${path}" ]]; then
    echo "PASS file   ${path}"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "MISS file   ${path}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

check_pattern() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if [[ -f "${file}" ]] && rg -q "${pattern}" "${file}"; then
    echo "PASS check  ${label}"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "MISS check  ${label}"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

echo "== Empyralis Parity Scan =="
echo "root: ${ROOT_DIR}"
echo

echo "-- Core files --"
check_file "scripts/orion_terminal/prompt_contracts.py"
check_file "scripts/orion_terminal/prompt_engine.py"
check_file "scripts/orion_terminal/widgets.py"
check_file "scripts/orion_terminal/core.py"
check_file "scripts/orion_terminal/flows_launcher.py"
check_file "scripts/orion_terminal/flows_orion_setup.py"
check_file "scripts/orion_terminal/flows_preflight.py"
check_file "scripts/orion_terminal/flows_configure.py"
check_file "scripts/orion_terminal/flows_onboard.py"
check_file "scripts/orion_terminal/flows_run.py"
check_file "scripts/orion_terminal/services/provider_registry.py"
check_file "scripts/orion_terminal/services/provider_credentials.py"
check_file "scripts/orion_terminal/services/connectors.py"
check_file "scripts/orion_terminal/services/live_tui.py"
check_file "server.py"
check_file "server_modules/runtime_status.py"
check_file "server_modules/doctor_report.py"
echo

echo "-- Flow prompt checks --"
check_pattern "scripts/orion_terminal/flows_orion_setup.py" "Gateway Location|Where will the Gateway run" "setup gateway location prompt"
check_pattern "scripts/orion_terminal/flows_orion_setup.py" "Select sections to configure|Configure Sections|Q_SETUP_SELECT_SECTIONS" "configure section selection prompt"
check_pattern "scripts/orion_terminal/flows_orion_setup.py" "Onboarding mode|Config handling|onboarding_mode|config_handling|Q_SETUP_ONBOARDING_MODE|Q_SETUP_CONFIG_HANDLING" "onboarding mode + config handling"
check_pattern "scripts/orion_terminal/services/provider_credentials.py" "Credential handling|OpenAI credential type|API key|OAuth|access token" "provider credential handling coverage"
check_pattern "scripts/orion_terminal/services/connectors.py" "telegram|whatsapp|google" "connector registry includes key channels"
check_pattern "scripts/orion_terminal/flows_run.py" "Run Goal|Change Trust Mode|Change Execution Target|Run Doctor" "live tui action coverage"
check_pattern "scripts/orion_terminal/widgets.py" "Enter select|Space toggle|Esc cancel|PgUp/PgDn" "selector key hints"
echo

echo "-- Runtime/auth checks --"
check_pattern "server.py" "OPENAI_ACCESS_TOKEN|OPENAI_OAUTH_TOKEN|CODEX_OAUTH_TOKEN" "runtime token env support"
check_pattern "server.py" "openai_supported_credential_fields" "runtime credential capability flags"
check_pattern "server_modules/runtime_status.py" "probe_openai_credential" "health probe extraction"
check_pattern "server_modules/doctor_report.py" "codex_signin_mode|openai_connectivity|auth_policy" "doctor checks coverage"
echo

echo "-- Quality gates --"
if bash scripts/lean_line_budget.sh --strict >/dev/null; then
  echo "PASS gate   lean_line_budget --strict"
  PASS_COUNT=$((PASS_COUNT + 1))
else
  echo "MISS gate   lean_line_budget --strict"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi

if python3 -m unittest discover -s scripts/orion_terminal/tests -p 'test_*.py' >/dev/null; then
  echo "PASS gate   terminal unit tests"
  PASS_COUNT=$((PASS_COUNT + 1))
else
  echo "MISS gate   terminal unit tests"
  FAIL_COUNT=$((FAIL_COUNT + 1))
fi
echo

echo "summary: pass=${PASS_COUNT} miss=${FAIL_COUNT}"
if [[ ${FAIL_COUNT} -gt 0 ]]; then
  exit 1
fi
