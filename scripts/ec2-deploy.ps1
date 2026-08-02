# Pushes your current local app.py + frontend/src to the EC2 box, rebuilds
# the frontend, and restarts the backend service -- the same manual steps
# used throughout this project's deploys, wrapped into one script. Run this
# after ec2-start.ps1 once the instance is confirmed reachable.
#
# Usage:  powershell -File scripts\ec2-deploy.ps1

$ErrorActionPreference = "Stop"

$SshHost   = "rapex-ec2"
$RemoteDir = "~/throughline"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "Copying app.py..."
scp "$ProjectRoot\app.py" "${SshHost}:${RemoteDir}/app.py"

Write-Host "Copying frontend/src..."
scp -r "$ProjectRoot\frontend\src" "${SshHost}:${RemoteDir}/frontend/"

Write-Host "`nBuilding frontend on the server..."
ssh $SshHost "cd $RemoteDir/frontend && npm run build"

Write-Host "`nRestarting the throughline service..."
ssh $SshHost "sudo systemctl restart throughline && sleep 2 && sudo systemctl is-active throughline"

Write-Host "`nHealth check..."
$status = (curl.exe -s -o NUL -w "%{http_code}" https://rapexapi.nodexdata.click/)
if ($status -eq "200") {
    Write-Host "Live and responding (200 OK). Ready to test." -ForegroundColor Green
} else {
    Write-Host "Unexpected status code: $status -- check the service logs (ssh $SshHost 'sudo journalctl -u throughline -n 50')." -ForegroundColor Red
}
