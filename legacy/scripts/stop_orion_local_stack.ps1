Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RootDir ".orion-stack"
$PidDir = Join-Path $StateDir "pids"

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
      if ([int]$Matches[1] -eq $Port) {
        $out += [int]$pidRaw
      }
    }
  }
  return $out | Sort-Object -Unique
}

function Stop-PidFile([string]$Name) {
  $file = Join-Path $PidDir "$Name.pid"
  if (-not (Test-Path $file)) { return }
  $raw = Get-Content $file -Raw -ErrorAction SilentlyContinue
  if ($raw) {
    $pid = 0
    if ([int]::TryParse($raw.Trim(), [ref]$pid) -and $pid -gt 0) {
      try {
        $proc = Get-Process -Id $pid -ErrorAction Stop
        Write-Host "Stopping $Name (pid $pid)"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
      } catch {}
    }
  }
  Remove-Item $file -Force -ErrorAction SilentlyContinue
}

Stop-PidFile "worker"
Stop-PidFile "frontend"
Stop-PidFile "backend"
Stop-PidFile "runtime"
Stop-PidFile "ops-daemon"

foreach ($port in @(3000, 4000, 8001, 8787)) {
  $pids = Get-PidsByPort -Port $port
  if ($pids.Count -gt 0) {
    Write-Host "Port cleanup on $port: $($pids -join ' ')"
    foreach ($pid in $pids) {
      try { Stop-Process -Id $pid -Force -ErrorAction Stop } catch {}
    }
  }
}

Remove-Item (Join-Path $StateDir "start.lock") -Force -ErrorAction SilentlyContinue

Write-Host "Empyralis local stack stopped."
