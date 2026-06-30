param(
  [string]$Target = "auto"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Test-Path (Join-Path $RootDir "src-tauri/Cargo.toml"))) {
  Write-Error "src-tauri/Cargo.toml not found."
}

Push-Location $RootDir
try {
  if (-not (Test-Path (Join-Path $RootDir "node_modules/@tauri-apps"))) {
    Write-Host "[setup] Installing desktop dependencies..."
    npm install
  }

  Write-Host "[build] Empyralis desktop target: $Target"
  npm run tauri:build
} finally {
  Pop-Location
}

Write-Host ""
Write-Host "Build output:"
Write-Host "  $RootDir/src-tauri/target"
