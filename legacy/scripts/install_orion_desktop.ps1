Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Test-Path (Join-Path $RootDir "src-tauri/Cargo.toml"))) {
  Write-Error "src-tauri/Cargo.toml not found."
}

Write-Host "[setup] Installing Empyralis Tauri desktop dependencies..."
Push-Location $RootDir
try {
  npm install
} finally {
  Pop-Location
}

Write-Host ""
Write-Host "Desktop dependencies installed."
Write-Host "Run:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/run_orion_desktop.ps1"
Write-Host ""
Write-Host "Build:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/build_orion_desktop.ps1"
