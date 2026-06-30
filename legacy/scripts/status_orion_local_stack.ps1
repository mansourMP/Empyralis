Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RootDir ".orion-stack"
$PidDir = Join-Path $StateDir "pids"
$LogDir = Join-Path $StateDir "logs"

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

function Show-PidState([string]$Name, [int]$FallbackPort) {
  $file = Join-Path $PidDir "$Name.pid"
  if (Test-Path $file) {
    $raw = Get-Content $file -Raw -ErrorAction SilentlyContinue
    $pid = 0
    if ([int]::TryParse(($raw.Trim()), [ref]$pid) -and $pid -gt 0) {
      try {
        $proc = Get-Process -Id $pid -ErrorAction Stop
        Write-Host "$Name`: running (pid $pid) $($proc.ProcessName)"
        return
      } catch {
        Write-Host "$Name`: stale pid file ($pid)"
        return
      }
    }
  }
  if ($FallbackPort -gt 0) {
    $pids = Get-PidsByPort -Port $FallbackPort
    if ($pids.Count -gt 0) {
      Write-Host "$Name`: running (port $FallbackPort => $($pids -join ' '))"
      return
    }
  }
  Write-Host "$Name`: not tracked"
}

Write-Host "== Empyralis local stack status =="
Show-PidState -Name "runtime" -FallbackPort 8001
Show-PidState -Name "backend" -FallbackPort 4000
Show-PidState -Name "frontend" -FallbackPort 3000
Show-PidState -Name "worker" -FallbackPort 0

Write-Host ""
Write-Host "Ports:"
foreach ($port in @(8001, 4000, 3000, 8787)) {
  $pids = Get-PidsByPort -Port $port
  if ($pids.Count -gt 0) {
    Write-Host "  $port`: in use ($($pids -join ' '))"
  } else {
    Write-Host "  $port`: free"
  }
}

Write-Host ""
Write-Host "Logs directory: $LogDir"
