param(
  [string]$RuntimeKey = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RootDir ".orion-stack"
$LogDir = Join-Path $StateDir "logs"
$PidDir = Join-Path $StateDir "pids"
$OpenAiKeyFile = Join-Path $StateDir "openai_api_key"

$OrionHost = if ($env:ORION_HOST) { $env:ORION_HOST } else { "127.0.0.1" }
$OrionPort = if ($env:ORION_PORT) { [int]$env:ORION_PORT } else { 8001 }
$BackendPort = if ($env:BACKEND_PORT) { [int]$env:BACKEND_PORT } else { 8080 }
$FrontendHost = if ($env:FRONTEND_HOST) { $env:FRONTEND_HOST } else { "127.0.0.1" }
$FrontendPort = if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 3000 }

$StartRuntime = if ($env:START_RUNTIME) { [int]$env:START_RUNTIME } else { 1 }
$StartBackend = if ($env:START_BACKEND) { [int]$env:START_BACKEND } else { 0 }
$StartFrontend = if ($env:START_FRONTEND) { [int]$env:START_FRONTEND } else { 1 }
$StartWorker = if ($env:START_WORKER) { [int]$env:START_WORKER } else { 1 }
$BackendMode = if ($env:BACKEND_MODE) { $env:BACKEND_MODE } else { "skip" }

$TelegramAutopilotEnabled = if ($env:EMPYRALIS_TELEGRAM_AUTOPILOT_ENABLED) { $env:EMPYRALIS_TELEGRAM_AUTOPILOT_ENABLED } elseif ($env:ORION_TELEGRAM_AUTOPILOT_ENABLED) { $env:ORION_TELEGRAM_AUTOPILOT_ENABLED } else { "1" }
$TelegramAutopilotProfile = if ($env:EMPYRALIS_TELEGRAM_AUTOPILOT_PROFILE) { $env:EMPYRALIS_TELEGRAM_AUTOPILOT_PROFILE } elseif ($env:ORION_TELEGRAM_AUTOPILOT_PROFILE) { $env:ORION_TELEGRAM_AUTOPILOT_PROFILE } else { "assistant" }
$TelegramAutopilotEngine = if ($env:EMPYRALIS_TELEGRAM_AUTOPILOT_ENGINE) { $env:EMPYRALIS_TELEGRAM_AUTOPILOT_ENGINE } elseif ($env:ORION_TELEGRAM_AUTOPILOT_ENGINE) { $env:ORION_TELEGRAM_AUTOPILOT_ENGINE } else { "codex" }
$TelegramAutopilotRequirePrefix = if ($env:EMPYRALIS_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX) { $env:EMPYRALIS_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX } elseif ($env:ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX) { $env:ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX } else { "0" }
$TelegramAutopilotAllowAnyChat = if ($env:EMPYRALIS_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT) { $env:EMPYRALIS_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT } elseif ($env:ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT) { $env:ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT } else { "0" }
$TelegramAutopilotExecutionTarget = if ($env:EMPYRALIS_TELEGRAM_AUTOPILOT_EXECUTION_TARGET) { $env:EMPYRALIS_TELEGRAM_AUTOPILOT_EXECUTION_TARGET } elseif ($env:ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET) { $env:ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET } else { "local_companion" }

$WhatsappAutopilotEnabled = if ($env:EMPYRALIS_WHATSAPP_AUTOPILOT_ENABLED) { $env:EMPYRALIS_WHATSAPP_AUTOPILOT_ENABLED } elseif ($env:ORION_WHATSAPP_AUTOPILOT_ENABLED) { $env:ORION_WHATSAPP_AUTOPILOT_ENABLED } else { "1" }
$WhatsappAutopilotProfile = if ($env:EMPYRALIS_WHATSAPP_AUTOPILOT_PROFILE) { $env:EMPYRALIS_WHATSAPP_AUTOPILOT_PROFILE } elseif ($env:ORION_WHATSAPP_AUTOPILOT_PROFILE) { $env:ORION_WHATSAPP_AUTOPILOT_PROFILE } else { "assistant" }
$WhatsappAutopilotEngine = if ($env:EMPYRALIS_WHATSAPP_AUTOPILOT_ENGINE) { $env:EMPYRALIS_WHATSAPP_AUTOPILOT_ENGINE } elseif ($env:ORION_WHATSAPP_AUTOPILOT_ENGINE) { $env:ORION_WHATSAPP_AUTOPILOT_ENGINE } else { "codex" }
$WhatsappAutopilotRequirePrefix = if ($env:EMPYRALIS_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX) { $env:EMPYRALIS_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX } elseif ($env:ORION_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX) { $env:ORION_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX } else { "0" }
$WhatsappAutopilotExecutionTarget = if ($env:EMPYRALIS_WHATSAPP_AUTOPILOT_EXECUTION_TARGET) { $env:EMPYRALIS_WHATSAPP_AUTOPILOT_EXECUTION_TARGET } elseif ($env:ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET) { $env:ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET } else { "local_companion" }

$AuthMode = if ($env:ORION_AUTH_MODE) { $env:ORION_AUTH_MODE } else { "codex" }
$DisableOpenAiApiKey = if ($env:ORION_DISABLE_OPENAI_API_KEY) { $env:ORION_DISABLE_OPENAI_API_KEY } else { if ($AuthMode -eq "api_key") { "0" } else { "1" } }

function Get-CommandPath([string]$Name) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if ($null -eq $cmd) { return $null }
  return $cmd.Source
}

function Get-PythonInvocation([string[]]$Args) {
  $python = Get-CommandPath "python"
  if ($python) { return @{ Cmd = $python; Args = $Args } }
  $python3 = Get-CommandPath "python3"
  if ($python3) { return @{ Cmd = $python3; Args = $Args } }
  $py = Get-CommandPath "py"
  if ($py) { return @{ Cmd = $py; Args = @("-3") + $Args } }
  throw "Python was not found in PATH."
}

function Get-PidsByPort([int]$Port) {
  $rows = netstat -ano -p tcp | Select-String "LISTENING"
  $out = @()
  foreach ($row in $rows) {
    $line = ($row.ToString()).Trim()
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $parts = $line -split "\s+"
    if ($parts.Length -lt 5) { continue }
    $local = $parts[1]
    $pidRaw = $parts[$parts.Length - 1]
    if ($local -match ":(\d+)$") {
      $p = [int]$Matches[1]
      if ($p -eq $Port) {
        $out += [int]$pidRaw
      }
    }
  }
  return $out | Sort-Object -Unique
}

function Stop-PortIfBusy([int]$Port) {
  $pids = Get-PidsByPort -Port $Port
  if ($pids.Count -gt 0) {
    Write-Host "Port $Port is busy. Stopping old process(es): $($pids -join ' ')"
    foreach ($pid in $pids) {
      try { Stop-Process -Id $pid -Force -ErrorAction Stop } catch {}
    }
  }
}

function Start-TrackedProcess([string]$Name, [string]$FilePath, [string[]]$Args, [string]$WorkingDir, [string]$LogPath) {
  $proc = Start-Process -FilePath $FilePath -ArgumentList $Args -WorkingDirectory $WorkingDir -PassThru -WindowStyle Hidden -RedirectStandardOutput $LogPath -RedirectStandardError $LogPath
  $pidFile = Join-Path $PidDir "$Name.pid"
  Set-Content -Path $pidFile -Value $proc.Id -NoNewline -Encoding ASCII
  return $proc
}

function Wait-HttpOk([string]$Url, [int]$Tries = 60, [int]$SleepMs = 500) {
  for ($i = 0; $i -lt $Tries; $i++) {
    try {
      $res = Invoke-WebRequest -Uri $Url -Method GET -UseBasicParsing -TimeoutSec 3
      if ($res.StatusCode -ge 200 -and $res.StatusCode -lt 300) { return $true }
    } catch {}
    Start-Sleep -Milliseconds $SleepMs
  }
  return $false
}

function Ensure-Dir([string]$Path) {
  if (-not (Test-Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

Ensure-Dir $StateDir
Ensure-Dir $LogDir
Ensure-Dir $PidDir

function New-RuntimeKey {
  $bytes = New-Object byte[] 24
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  return ([System.BitConverter]::ToString($bytes)).Replace("-", "").ToLowerInvariant()
}

$rawRuntimeKey = if ($RuntimeKey) { $RuntimeKey } elseif ($env:RUNTIME_KEY) { $env:RUNTIME_KEY } elseif ($env:ORION_API_KEY) { $env:ORION_API_KEY } else { "" }
$cleanRuntimeKey = ($rawRuntimeKey -replace "\s+", "")
if ([string]::IsNullOrWhiteSpace($cleanRuntimeKey)) {
  $cleanRuntimeKey = New-RuntimeKey
  Write-Host "No runtime key supplied. Generated a random local runtime key."
}
if ($rawRuntimeKey -ne $cleanRuntimeKey) { Write-Host "Warning: runtime key contained whitespace and was normalized." }

$runtimeUrl = "http://$OrionHost`:$OrionPort"
$backendUrl = "http://127.0.0.1:$BackendPort"
$frontendUrl = "http://$FrontendHost`:$FrontendPort"

Set-Content -Path (Join-Path $StateDir "runtime_key") -Value $cleanRuntimeKey -NoNewline -Encoding ASCII

if ([string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY) -and (Test-Path $OpenAiKeyFile)) {
  $persisted = Get-Content $OpenAiKeyFile -Raw -ErrorAction SilentlyContinue
  if (-not [string]::IsNullOrWhiteSpace($persisted)) {
    $env:OPENAI_API_KEY = $persisted.Trim()
  }
}
if (-not [string]::IsNullOrWhiteSpace($env:OPENAI_API_KEY)) {
  Set-Content -Path $OpenAiKeyFile -Value $env:OPENAI_API_KEY -NoNewline -Encoding ASCII
}

$stackEnv = @(
  "EMPYRALIS_TELEGRAM_AUTOPILOT_ENABLED=$TelegramAutopilotEnabled"
  "EMPYRALIS_TELEGRAM_AUTOPILOT_PROFILE=$TelegramAutopilotProfile"
  "EMPYRALIS_TELEGRAM_AUTOPILOT_ENGINE=$TelegramAutopilotEngine"
  "EMPYRALIS_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX=$TelegramAutopilotRequirePrefix"
  "EMPYRALIS_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT=$TelegramAutopilotAllowAnyChat"
  "EMPYRALIS_TELEGRAM_AUTOPILOT_EXECUTION_TARGET=$TelegramAutopilotExecutionTarget"
  "ORION_TELEGRAM_AUTOPILOT_ENABLED=$TelegramAutopilotEnabled"
  "ORION_TELEGRAM_AUTOPILOT_PROFILE=$TelegramAutopilotProfile"
  "ORION_TELEGRAM_AUTOPILOT_ENGINE=$TelegramAutopilotEngine"
  "ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX=$TelegramAutopilotRequirePrefix"
  "ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT=$TelegramAutopilotAllowAnyChat"
  "ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET=$TelegramAutopilotExecutionTarget"
  "EMPYRALIS_WHATSAPP_AUTOPILOT_ENABLED=$WhatsappAutopilotEnabled"
  "EMPYRALIS_WHATSAPP_AUTOPILOT_PROFILE=$WhatsappAutopilotProfile"
  "EMPYRALIS_WHATSAPP_AUTOPILOT_ENGINE=$WhatsappAutopilotEngine"
  "EMPYRALIS_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX=$WhatsappAutopilotRequirePrefix"
  "EMPYRALIS_WHATSAPP_AUTOPILOT_EXECUTION_TARGET=$WhatsappAutopilotExecutionTarget"
  "ORION_WHATSAPP_AUTOPILOT_ENABLED=$WhatsappAutopilotEnabled"
  "ORION_WHATSAPP_AUTOPILOT_PROFILE=$WhatsappAutopilotProfile"
  "ORION_WHATSAPP_AUTOPILOT_ENGINE=$WhatsappAutopilotEngine"
  "ORION_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX=$WhatsappAutopilotRequirePrefix"
  "ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET=$WhatsappAutopilotExecutionTarget"
  "ORION_AUTH_MODE=$AuthMode"
  "ORION_DISABLE_OPENAI_API_KEY=$DisableOpenAiApiKey"
)
$stackEnv | Set-Content -Path (Join-Path $StateDir "stack.env") -Encoding ASCII

if ($StartRuntime -eq 1) { Stop-PortIfBusy -Port $OrionPort }
if ($StartBackend -eq 1) { Stop-PortIfBusy -Port $BackendPort }
if ($StartFrontend -eq 1) { Stop-PortIfBusy -Port $FrontendPort }

$runtimeLog = Join-Path $LogDir "runtime.log"
$backendLog = Join-Path $LogDir "backend.log"
$frontendLog = Join-Path $LogDir "frontend.log"
$workerLog = Join-Path $LogDir "worker.log"

if ($StartRuntime -eq 1) {
  Write-Host "Starting Empyralis runtime on $OrionHost`:$OrionPort ..."
  $env:ORION_AUTH_REQUIRED = "1"
  $env:ORION_API_KEY = $cleanRuntimeKey
  $env:ORION_LOCAL_COMPANION_ENABLED = "1"
  $env:EMPYRALIS_TELEGRAM_AUTOPILOT_ENABLED = $TelegramAutopilotEnabled
  $env:EMPYRALIS_TELEGRAM_AUTOPILOT_PROFILE = $TelegramAutopilotProfile
  $env:EMPYRALIS_TELEGRAM_AUTOPILOT_ENGINE = $TelegramAutopilotEngine
  $env:EMPYRALIS_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX = $TelegramAutopilotRequirePrefix
  $env:EMPYRALIS_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT = $TelegramAutopilotAllowAnyChat
  $env:EMPYRALIS_TELEGRAM_AUTOPILOT_EXECUTION_TARGET = $TelegramAutopilotExecutionTarget
  $env:ORION_TELEGRAM_AUTOPILOT_ENABLED = $TelegramAutopilotEnabled
  $env:ORION_TELEGRAM_AUTOPILOT_PROFILE = $TelegramAutopilotProfile
  $env:ORION_TELEGRAM_AUTOPILOT_ENGINE = $TelegramAutopilotEngine
  $env:ORION_TELEGRAM_AUTOPILOT_REQUIRE_PREFIX = $TelegramAutopilotRequirePrefix
  $env:ORION_TELEGRAM_AUTOPILOT_ALLOW_ANY_CHAT = $TelegramAutopilotAllowAnyChat
  $env:ORION_TELEGRAM_AUTOPILOT_EXECUTION_TARGET = $TelegramAutopilotExecutionTarget
  $env:EMPYRALIS_WHATSAPP_AUTOPILOT_ENABLED = $WhatsappAutopilotEnabled
  $env:EMPYRALIS_WHATSAPP_AUTOPILOT_PROFILE = $WhatsappAutopilotProfile
  $env:EMPYRALIS_WHATSAPP_AUTOPILOT_ENGINE = $WhatsappAutopilotEngine
  $env:EMPYRALIS_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX = $WhatsappAutopilotRequirePrefix
  $env:EMPYRALIS_WHATSAPP_AUTOPILOT_EXECUTION_TARGET = $WhatsappAutopilotExecutionTarget
  $env:ORION_WHATSAPP_AUTOPILOT_ENABLED = $WhatsappAutopilotEnabled
  $env:ORION_WHATSAPP_AUTOPILOT_PROFILE = $WhatsappAutopilotProfile
  $env:ORION_WHATSAPP_AUTOPILOT_ENGINE = $WhatsappAutopilotEngine
  $env:ORION_WHATSAPP_AUTOPILOT_REQUIRE_PREFIX = $WhatsappAutopilotRequirePrefix
  $env:ORION_WHATSAPP_AUTOPILOT_EXECUTION_TARGET = $WhatsappAutopilotExecutionTarget
  $env:ORION_AUTH_MODE = $AuthMode
  $env:ORION_DISABLE_OPENAI_API_KEY = $DisableOpenAiApiKey
  $env:OPENAI_HEALTHCHECK = if ($env:OPENAI_HEALTHCHECK) { $env:OPENAI_HEALTHCHECK } else { "0" }

  $pyRuntime = Get-PythonInvocation @("-u", "-m", "uvicorn", "server:app", "--host", $OrionHost, "--port", "$OrionPort")
  Start-TrackedProcess -Name "runtime" -FilePath $pyRuntime.Cmd -Args $pyRuntime.Args -WorkingDir $RootDir -LogPath $runtimeLog | Out-Null
} else {
  Write-Host "Skipping runtime startup (START_RUNTIME=0)."
}

$backendEffectiveMode = $BackendMode
if ($StartBackend -ne 1) {
  $backendEffectiveMode = "skip"
}
if ($StartBackend -eq 1) {
  $backendEffectiveMode = "skip"
  throw "Legacy backend/ sidecar is not an active launch target; use the Python Empyralis runtime on $runtimeUrl."
} else {
  Write-Host "Skipping legacy backend startup (START_BACKEND=0)."
}

if ($StartFrontend -eq 1) {
  Write-Host "Starting frontend on $FrontendHost`:$FrontendPort ..."
  $env:NEXT_PUBLIC_ORION_API_URL = $runtimeUrl
  $npm = Get-CommandPath "npm"
  if (-not $npm) { throw "npm was not found in PATH." }
  Start-TrackedProcess -Name "frontend" -FilePath $npm -Args @("run", "dev", "--", "--hostname", $FrontendHost, "--port", "$FrontendPort") -WorkingDir (Join-Path $RootDir "frontend") -LogPath $frontendLog | Out-Null
} else {
  Write-Host "Skipping frontend startup (START_FRONTEND=0)."
}

if ($StartWorker -eq 1) {
  Write-Host "Starting local worker ..."
  $hostId = (($env:COMPUTERNAME).ToLower() -replace '[^a-z0-9]+', '-').Trim('-')
  if ([string]::IsNullOrWhiteSpace($hostId)) { $hostId = "windows" }
  $workerId = "empyralis-local-$hostId"
  $pollSeconds = if ($env:ORION_LOCAL_WORKER_POLL_SECONDS) { $env:ORION_LOCAL_WORKER_POLL_SECONDS } else { "3.0" }
  $idleHeartbeatSeconds = if ($env:ORION_LOCAL_WORKER_IDLE_HEARTBEAT_SECONDS) { $env:ORION_LOCAL_WORKER_IDLE_HEARTBEAT_SECONDS } else { "15.0" }

  $pyWorker = Get-PythonInvocation @(
    "-u",
    "scripts/orion_local_worker.py",
    "--runtime-url", $runtimeUrl,
    "--api-key", $cleanRuntimeKey,
    "--poll-seconds", $pollSeconds,
    "--idle-heartbeat-seconds", $idleHeartbeatSeconds,
    "--worker-id", $workerId
  )
  Start-TrackedProcess -Name "worker" -FilePath $pyWorker.Cmd -Args $pyWorker.Args -WorkingDir $RootDir -LogPath $workerLog | Out-Null
} else {
  Write-Host "Skipping local worker startup (START_WORKER=0)."
}

Write-Host "Waiting for services..."
if ($StartRuntime -eq 1) {
  if (-not (Wait-HttpOk -Url "$runtimeUrl/health" -Tries 80 -SleepMs 400)) {
    throw "Runtime did not become ready. Check: $runtimeLog"
  }
}
if ($backendEffectiveMode -ne "skip") {
  if (-not (Wait-HttpOk -Url "$backendUrl/api/v1/health" -Tries 90 -SleepMs 450)) {
    throw "Backend did not become ready (mode=$backendEffectiveMode). Check: $backendLog"
  }
}
if ($StartFrontend -eq 1) {
  if (-not (Wait-HttpOk -Url "http://127.0.0.1:$FrontendPort" -Tries 90 -SleepMs 450)) {
    throw "Frontend did not become ready. Check: $frontendLog"
  }
}

Write-Host ""
Write-Host "Empyralis local stack is up."
Write-Host "Runtime:  $runtimeUrl"
Write-Host "Legacy backend: removed from the active launch path"
Write-Host "Frontend: $frontendUrl"
Write-Host "Modes:    runtime=$StartRuntime backend=$backendEffectiveMode frontend=$StartFrontend worker=$StartWorker telegram_autopilot=$TelegramAutopilotEnabled whatsapp_autopilot=$WhatsappAutopilotEnabled"
Write-Host "Telegram: profile=$TelegramAutopilotProfile"
Write-Host "Telegram: engine=$TelegramAutopilotEngine"
Write-Host "Telegram: prefix_required=$TelegramAutopilotRequirePrefix"
Write-Host "Telegram: allow_any_chat=$TelegramAutopilotAllowAnyChat"
Write-Host "Telegram: execution_target=$TelegramAutopilotExecutionTarget"
Write-Host "WhatsApp: profile=$WhatsappAutopilotProfile"
Write-Host "WhatsApp: engine=$WhatsappAutopilotEngine"
Write-Host "WhatsApp: prefix_required=$WhatsappAutopilotRequirePrefix"
Write-Host "WhatsApp: execution_target=$WhatsappAutopilotExecutionTarget"
Write-Host "Auth: mode=$AuthMode disable_openai_api_key=$DisableOpenAiApiKey"
Write-Host ""
Write-Host "Runtime key: $cleanRuntimeKey"
Write-Host ""
Write-Host "Health checks:"
Write-Host "  curl -s -H `"X-API-Key: $cleanRuntimeKey`" $runtimeUrl/health"
Write-Host "  curl -s -H `"X-API-Key: $cleanRuntimeKey`" `"$runtimeUrl/runtime/runtimes/status`""
Write-Host "  curl -s -H `"X-API-Key: $cleanRuntimeKey`" `"$runtimeUrl/connectors/vault?workspace_id=default`""
Write-Host "  curl -s -H `"X-API-Key: $cleanRuntimeKey`" `"$runtimeUrl/channels/telegram/autopilot/status`""
Write-Host ""
Write-Host "Logs:"
Write-Host "  Get-Content -Path `"$runtimeLog`" -Tail 100 -Wait"
Write-Host "  Get-Content -Path `"$frontendLog`" -Tail 100 -Wait"
Write-Host "  Get-Content -Path `"$workerLog`" -Tail 100 -Wait"
Write-Host ""
Write-Host "To stop all:"
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/stop_orion_local_stack.ps1"
