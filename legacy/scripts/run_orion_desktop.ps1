Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$FrontendUrl = if ($env:ORION_DESKTOP_FRONTEND_URL) { $env:ORION_DESKTOP_FRONTEND_URL } else { "http://127.0.0.1:3000" }
$RuntimeUrl = if ($env:ORION_DESKTOP_RUNTIME_URL) { $env:ORION_DESKTOP_RUNTIME_URL } else { "http://127.0.0.1:8001/health" }
$StartStackScript = Join-Path $RootDir "scripts/start_empyralis_local_stack.sh"
if (-not (Test-Path $StartStackScript)) {
  $StartStackScript = Join-Path $RootDir "scripts/start_orion_local_stack.sh"
}

if (-not (Test-Path (Join-Path $RootDir "src-tauri/Cargo.toml"))) {
  Write-Error "src-tauri/Cargo.toml not found."
}

function Test-HttpReady {
  param([string]$Url)
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $Url -TimeoutSec 5
    return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
  } catch {
    return $false
  }
}

if (-not (Test-HttpReady $FrontendUrl) -or -not (Test-HttpReady $RuntimeUrl)) {
  Write-Host "[setup] Local stack is not fully ready. Starting Empyralis services..."
  & $StartStackScript
  if (-not (Test-HttpReady $FrontendUrl)) {
    throw "Frontend did not become ready at $FrontendUrl"
  }
  if (-not (Test-HttpReady $RuntimeUrl)) {
    throw "Runtime did not become ready at $RuntimeUrl"
  }
}

Push-Location $RootDir
try {
  if (-not (Test-Path (Join-Path $RootDir "node_modules/@tauri-apps"))) {
    Write-Host "[setup] Installing desktop dependencies..."
    npm install
  }

  Write-Host "[run] Launching Empyralis Tauri desktop..."
  npm run tauri:dev
} finally {
  Pop-Location
}
