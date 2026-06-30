#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

MODE="${1:---strict}"
STRICT=0
if [[ "${MODE}" == "--strict" ]]; then
  STRICT=1
elif [[ "${MODE}" == "--report-only" || -z "${MODE}" ]]; then
  STRICT=0
else
  echo "Usage: bash scripts/lean_line_budget.sh [--report-only|--strict]"
  exit 2
fi

echo "== Empyralis Lean Line Budget =="
echo "root: ${ROOT_DIR}"
echo "mode: $([[ ${STRICT} -eq 1 ]] && echo strict || echo report-only)"
echo

violations=0

check_budget() {
  local file="$1"
  local max_lines="$2"
  if [[ ! -f "${file}" ]]; then
    printf "skip   missing file: %s\n" "${file}"
    return
  fi
  local lines
  lines="$(wc -l < "${file}" | tr -d ' ')"
  if [[ "${lines}" -gt "${max_lines}" ]]; then
    printf "VIOL   %5s > %-5s  %s\n" "${lines}" "${max_lines}" "${file}"
    violations=$((violations + 1))
  else
    printf "OK     %5s <= %-5s %s\n" "${lines}" "${max_lines}" "${file}"
  fi
}

# Active hotspot budgets (strict by default).
check_budget "server.py" 6500
check_budget "frontend/app/page.tsx" 3000
check_budget "frontend/app/workflows/[id]/WorkflowEditorInnerPro.tsx" 1600
check_budget "scripts/orion_local_worker.py" 600
check_budget "scripts/orion_terminal/core.py" 850
check_budget "scripts/orion_terminal/flows_run.py" 800
check_budget "scripts/orion_terminal/flows_orion_setup.py" 650
check_budget "scripts/orion_terminal/flows_preflight.py" 300
check_budget "scripts/orion_terminal/services/live_tui.py" 400
check_budget "scripts/orion_terminal/clients/runtime.py" 500
check_budget "server_modules/doctor_report.py" 450
check_budget "server_modules/runtime_status.py" 250

echo
if [[ "${violations}" -gt 0 ]]; then
  echo "budget_violations: ${violations}"
  if [[ "${STRICT}" -eq 1 ]]; then
    exit 1
  fi
else
  echo "budget_violations: 0"
fi
