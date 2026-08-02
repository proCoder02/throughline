# Starts the Flask backend detached in the background, so it keeps running
# without a dedicated terminal window while you build the React/Flutter
# clients against it. Debug mode (auto-reload on code changes) stays on --
# see backend-stop.ps1 for why that means killing the whole process tree,
# not just one PID, when you want to stop it.
#
# Usage:  powershell -File scripts\backend-start.ps1

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile     = "$ProjectRoot\.backend.pid"
$OutLog      = "$ProjectRoot\flask_out.log"
$ErrLog      = "$ProjectRoot\flask_err.log"
$Python      = "$ProjectRoot\.venv\Scripts\python.exe"

if (Test-Path $PidFile) {
    $existingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Backend already running (PID $existingPid)." -ForegroundColor Yellow
        Write-Host "Run backend-stop.ps1 first if you want to restart it."
        exit 0
    }
}

if (-not (Test-Path $Python)) {
    Write-Host "Couldn't find $Python -- is the venv set up? (python -m venv .venv && .venv\Scripts\pip install -r requirements.txt)" -ForegroundColor Red
    exit 1
}

Write-Host "Starting backend..."
$proc = Start-Process -FilePath $Python -ArgumentList "app.py" `
    -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog

$proc.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline

Start-Sleep -Seconds 3
$status = try { (Invoke-WebRequest -Uri "http://127.0.0.1:5000/" -UseBasicParsing -TimeoutSec 5).StatusCode } catch { $null }

if ($status -eq 200) {
    $lanIp = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        $_.InterfaceAlias -notmatch 'Loopback|vEthernet|WSL' -and $_.PrefixOrigin -eq 'Dhcp'
    } | Select-Object -First 1 -ExpandProperty IPAddress)
    Write-Host "Backend is up (PID $($proc.Id))." -ForegroundColor Green
    Write-Host "  Local:  http://127.0.0.1:5000"
    if ($lanIp) { Write-Host "  LAN:    http://${lanIp}:5000  (for a physical device on the same Wi-Fi)" }
    Write-Host "  Logs:   $OutLog / $ErrLog"
} else {
    Write-Host "Backend process started (PID $($proc.Id)) but isn't responding yet -- check $ErrLog." -ForegroundColor Red
}
