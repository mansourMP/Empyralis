#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

FILES=(
  "frontend/app/globals.css"
  "frontend/lib/brand.ts"
)

for file in "${FILES[@]}"; do
  if [[ ! -f "${file}" ]]; then
    echo "missing required file: ${file}"
    exit 2
  fi
done

required_pairs=(
  "frontend/app/globals.css:--primary-base: #6D28D9;"
  "frontend/app/globals.css:--primary-hover: #8B5CF6;"
  "frontend/app/globals.css:--warning-base: #F59E0B;"
  "frontend/lib/brand.ts:accentColor: '#6D28D9'"
  "frontend/lib/brand.ts:highlightColor: '#8B5CF6'"
  "frontend/lib/brand.ts:warningColor: '#F59E0B'"
)

for pair in "${required_pairs[@]}"; do
  file="${pair%%:*}"
  pattern="${pair#*:}"
  if ! rg -q --fixed-strings -- "${pattern}" "${file}"; then
    echo "missing brand token '${pattern}' in ${file}"
    exit 3
  fi
done

forbidden_patterns=(
  "#7a4bc2"
  "#6639af"
  "#d06b3d"
  "#e58a2f"
  "#9d4edd"
  "rgba(122, 75, 194"
  "rgba(193, 95, 60"
  "rgba(208, 107, 61"
)

violations=""
for pat in "${forbidden_patterns[@]}"; do
  matches="$(rg -n -i --fixed-strings "${pat}" frontend/app/globals.css frontend/lib/brand.ts || true)"
  if [[ -n "${matches}" ]]; then
    if [[ -n "${violations}" ]]; then
      violations+=$'\n'
    fi
    violations+="${matches}"
  fi
done

if [[ -n "${violations}" ]]; then
  echo "forbidden legacy brand colors found:"
  echo "${violations}"
  exit 4
fi

echo "brand_token_audit: PASS"
echo "primary=#6D28D9 highlight=#8B5CF6 warning=#F59E0B"
