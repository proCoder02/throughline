# Stops the backend started by backend-start.ps1. Uses taskkill /T (whole
# process tree) rather than Stop-Process on just the tracked PID -- Flask's
# debug-mode reloader spawns a real child OS process, and Stop-Process alone
# leaves that child running (this bit us once already this session: 4 stray
# app.py processes had to be found and killed by hand before a DB restore).
#
# Usage:  powershell -File scripts\backend-stop.ps1

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile     = "$ProjectRoot\.backend.pid"
$stoppedAny  = $false

if (Test-Path $PidFile) {
    $targetPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($targetPid -and (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
        taskkill /PID $targetPid /T /F 2>$null | Out-Null
        Write-Host "Stopped backend (PID $targetPid)." -ForegroundColor Green
        $stoppedAny = $true
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

# Fallback sweep: catches anything the PID file missed (started manually,
# or a reloader child that outlived its tracked parent).
$stray = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match [regex]::Escape("$ProjectRoot\app.py") -or $_.CommandLine -match "app\.py\s*$" }
if ($stray) {
    foreach ($p in $stray) { taskkill /PID $p.ProcessId /T /F 2>$null | Out-Null }
    Write-Host "Cleaned up $($stray.Count) additional stray app.py process(es)." -ForegroundColor Green
    $stoppedAny = $true
}

if (-not $stoppedAny) {
    Write-Host "Nothing was running."
}
