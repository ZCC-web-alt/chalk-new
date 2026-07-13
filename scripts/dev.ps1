param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Source = Join-Path $Root "src"

function Test-PortInUse([int]$Port) {
  return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1)
}

$occupied = @()
if (Test-PortInUse $BackendPort) { $occupied += $BackendPort }
if (Test-PortInUse $FrontendPort) { $occupied += $FrontendPort }
if ($occupied.Count -gt 0) {
  Write-Host "Cannot start Chalk Web because these ports are already in use: $($occupied -join ', ')." -ForegroundColor Red
  exit 1
}

Write-Host "Starting Chalk Web backend on http://127.0.0.1:$BackendPort" -ForegroundColor Cyan
Write-Host "Starting Chalk Web frontend on http://127.0.0.1:$FrontendPort" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop both servers." -ForegroundColor DarkGray

$backendJob = Start-Job -Name "chalk-web-backend" -ScriptBlock {
  param($Backend, $Source, $Port)
  Set-Location $Backend
  $pythonPaths = @($Backend, $Source)
  if ($env:PYTHONPATH) { $pythonPaths += $env:PYTHONPATH }
  $env:PYTHONPATH = $pythonPaths -join [IO.Path]::PathSeparator
  python -m uvicorn app.main:app --reload --host 127.0.0.1 --port $Port
} -ArgumentList $Backend, $Source, $BackendPort

$frontendJob = Start-Job -Name "chalk-web-frontend" -ScriptBlock {
  param($Frontend, $Port, $BackendPort)
  Set-Location $Frontend
  $env:NEXT_PUBLIC_CHALK_API_BASE = "http://127.0.0.1:$BackendPort/api"
  corepack pnpm dev --hostname 127.0.0.1 --port $Port
} -ArgumentList $Frontend, $FrontendPort, $BackendPort

try {
  while ($true) {
    Receive-Job -Job $backendJob, $frontendJob -ErrorAction Continue
    $stopped = @($backendJob, $frontendJob) | Where-Object { $_.State -in @("Completed", "Failed", "Stopped") }
    if ($stopped.Count -gt 0) {
      Receive-Job -Job $stopped -ErrorAction Continue
      throw "A Chalk Web development server stopped unexpectedly."
    }
    Start-Sleep -Seconds 1
  }
}
finally {
  Write-Host "Stopping Chalk Web servers..." -ForegroundColor Yellow
  Stop-Job -Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
  Remove-Job -Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
}
