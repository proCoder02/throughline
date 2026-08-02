# Quick check: is the local backend running and actually responding.
#
# Usage:  powershell -File scripts\backend-status.ps1

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile     = "$ProjectRoot\.backend.pid"

if ((Test-Path $PidFile) -and (Get-Process -Id (Get-Content $PidFile -ErrorAction SilentlyContinue) -ErrorAction SilentlyContinue)) {
    Write-Host "Process:  running (PID $(Get-Content $PidFile))" -ForegroundColor Green
} else {
    Write-Host "Process:  not running (no active PID file)" -ForegroundColor Yellow
}

try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:5000/" -UseBasicParsing -TimeoutSec 3
    Write-Host "HTTP:     $($r.StatusCode) OK" -ForegroundColor Green
} catch {
    Write-Host "HTTP:     not responding" -ForegroundColor Red
}
