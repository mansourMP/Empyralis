#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "== Empyralis Lean Repo Audit =="
echo "root: ${ROOT_DIR}"
echo

echo "-- Top-level directory sizes --"
du -sh .orion-stack .orion archive reference scripts docs frontend python_engine 2>/dev/null | sort -h || true
echo

echo "-- Active source files by line count (excluding reference/archive/generated) --"
find . \
  \( -type d \( \
      -name node_modules -o -name .git -o -name .next -o -name venv -o -name .venv -o \
      -path "./reference" -o -path "./archive" -o -path "./.orion-stack" -o -path "./screenshots" \
    \) -prune \) -o \
  \( -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.sh' \) -print0 \) \
  | xargs -0 wc -l | sort -nr | sed -n '1,60p'
echo

echo "-- Active source files >= 800 lines --"
find . \
  \( -type d \( \
      -name node_modules -o -name .git -o -name .next -o -name venv -o -name .venv -o \
      -path "./reference" -o -path "./archive" -o -path "./.orion-stack" -o -path "./screenshots" \
    \) -prune \) -o \
  \( -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.sh' \) -print0 \) \
  | xargs -0 wc -l | awk '$1>=800 {print $1 " " $2}' | sort -nr || true
echo

echo "-- Empyralis terminal file sizes --"
wc -l scripts/orion_terminal/*.py | sort -nr
echo

echo "-- Runtime hotspots --"
for p in \
  "server.py" \
  "frontend/app/page.tsx" \
  "frontend/app/workflows/[id]/WorkflowEditorInnerPro.tsx" \
  "scripts/orion_local_worker.py" \
  "scripts/orion_terminal/core.py"; do
  if [[ -f "$p" ]]; then
    printf "%6s  %s\n" "$(wc -l < "$p" | tr -d ' ')" "$p"
  fi
done
echo

echo "-- Runtime critical entrypoints --"
for p in \
  "server.py" \
  "bin/orion" \
  "scripts/start_orion_local_stack.sh" \
  "scripts/stop_orion_local_stack.sh" \
  "scripts/status_orion_local_stack.sh" \
  "scripts/orion_terminal_wizard.py" \
  "scripts/orion_local_worker.py"; do
  if [[ -f "$p" ]]; then
    echo "ok: $p"
  else
    echo "missing: $p"
  fi
done
echo

echo "-- Lean budget gate (strict) --"
if [[ -x "${ROOT_DIR}/scripts/lean_line_budget.sh" ]]; then
  bash "${ROOT_DIR}/scripts/lean_line_budget.sh" --strict
else
  echo "missing: scripts/lean_line_budget.sh"
fi
echo

echo "done"
