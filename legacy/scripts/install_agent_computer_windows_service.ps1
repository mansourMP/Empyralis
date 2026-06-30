param(
  [ValidateSet("install", "uninstall", "status", "run")]
  [string]$Action = "status",
  [string]$ServiceName = "EmpyralisAgentComputer",
  [string]$DisplayName = "Empyralis Agent Computer",
  [string]$ServiceUser = "",
  [string]$PairingToken = "",
  [string]$GatewayToken = "",
  [switch]$NoBuild,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Assert-Admin {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Windows Service actions must run from an elevated PowerShell session."
  }
}

function Write-AgentEnv {
  param([string]$Root)
  $stateDir = Join-Path $Root ".orion-stack\agent-computer"
  New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
  $envFile = Join-Path $stateDir "windows-service-env.ps1"
  $lines = @(
    '$env:EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE = "1"',
    '$env:EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET = "server_vps"'
  )
  if ($PairingToken) {
    $escaped = $PairingToken.Replace('"', '\"')
    $lines += "`$env:EMPYRALIS_GATEWAY_PAIRING_TOKEN = `"$escaped`""
  }
  if ($GatewayToken) {
    $escaped = $GatewayToken.Replace('"', '\"')
    $lines += "`$env:EMPYRALIS_GATEWAY_TOKEN = `"$escaped`""
  }
  Set-Content -Path $envFile -Value ($lines -join [Environment]::NewLine) -Encoding UTF8
  try {
    icacls $envFile /inheritance:r /grant:r "$env:USERNAME:F" "Administrators:F" "SYSTEM:F" | Out-Null
  } catch {
    Write-Warning "Could not tighten ACL on $envFile: $($_.Exception.Message)"
  }
  return $envFile
}

function Build-AgentComputer {
  param([string]$Root)
  if ($NoBuild) {
    return
  }
  Push-Location $Root
  try {
    npm --prefix empyralis-gateway install
    npm --prefix empyralis-gateway run build
    cargo build --release --manifest-path empyralis-supervisor\Cargo.toml
  } finally {
    Pop-Location
  }
}

function Install-AgentService {
  $root = Get-RepoRoot
  $script = Join-Path $root "scripts\install_agent_computer_windows_service.ps1"
  $binary = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$script`" -Action run"
  if ($DryRun) {
    [pscustomobject]@{
      service_name = $ServiceName
      display_name = $DisplayName
      binary_path = $binary
      root = $root
      system_service_mode = $true
      service_target = "server_vps"
    } | ConvertTo-Json -Depth 4
    return
  }
  Assert-Admin
  Build-AgentComputer -Root $root
  Write-AgentEnv -Root $root | Out-Null
  $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if ($existing) {
    throw "Service $ServiceName already exists. Use -Action uninstall first."
  }
  $credential = $null
  if ($ServiceUser) {
    $credential = Get-Credential -UserName $ServiceUser -Message "Credentials for $ServiceName"
  }
  if ($credential) {
    New-Service -Name $ServiceName -DisplayName $DisplayName -BinaryPathName $binary -StartupType Automatic -Credential $credential | Out-Null
  } else {
    New-Service -Name $ServiceName -DisplayName $DisplayName -BinaryPathName $binary -StartupType Automatic | Out-Null
  }
  Start-Service -Name $ServiceName
  Get-Service -Name $ServiceName | Format-List Name,DisplayName,Status,StartType
}

function Uninstall-AgentService {
  if ($DryRun) {
    Write-Output "Would stop and delete Windows Service $ServiceName."
    return
  }
  Assert-Admin
  $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if (-not $existing) {
    Write-Output "Service $ServiceName is not installed."
    return
  }
  if ($existing.Status -ne "Stopped") {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
  }
  sc.exe delete $ServiceName | Out-Null
  Write-Output "Deleted Windows Service $ServiceName."
}

function Show-AgentServiceStatus {
  $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
  if (-not $existing) {
    Write-Output "Service $ServiceName is not installed."
    return
  }
  $existing | Format-List Name,DisplayName,Status,StartType
}

function Run-AgentService {
  $root = Get-RepoRoot
  $envFile = Join-Path $root ".orion-stack\agent-computer\windows-service-env.ps1"
  if (Test-Path $envFile) {
    . $envFile
  }
  $env:EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE = "1"
  $env:EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET = "server_vps"
  $supervisor = Join-Path $root "empyralis-supervisor\target\release\empyralis-supervisor.exe"
  $gateway = Join-Path $root "empyralis-gateway\dist\index.js"
  if (-not (Test-Path $supervisor)) {
    throw "Supervisor binary missing at $supervisor. Run -Action install without -NoBuild."
  }
  if (-not (Test-Path $gateway)) {
    throw "Gateway build missing at $gateway. Run -Action install without -NoBuild."
  }
  $supervisorProcess = Start-Process -FilePath $supervisor -WorkingDirectory $root -PassThru
  try {
    while ($true) {
      $gatewayProcess = Start-Process -FilePath "node.exe" -ArgumentList @($gateway) -WorkingDirectory $root -PassThru -Wait
      Start-Sleep -Seconds 5
    }
  } finally {
    if ($supervisorProcess -and -not $supervisorProcess.HasExited) {
      Stop-Process -Id $supervisorProcess.Id -Force -ErrorAction SilentlyContinue
    }
  }
}

switch ($Action) {
  "install" { Install-AgentService }
  "uninstall" { Uninstall-AgentService }
  "status" { Show-AgentServiceStatus }
  "run" { Run-AgentService }
}
