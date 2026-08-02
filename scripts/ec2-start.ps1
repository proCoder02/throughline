# Starts the EC2 instance and RDS instance back up, and waits until the EC2
# box is actually reachable over SSH before returning -- so you can chain
# straight into ec2-deploy.ps1 without guessing whether it's ready yet.
#
# Usage:  powershell -File scripts\ec2-start.ps1

$ErrorActionPreference = "Stop"

$Region     = "eu-north-1"
$InstanceId = "i-072634a707e093ae3"          # rapex_ec2
$RdsId      = "REPLACE_WITH_YOUR_RDS_DB_IDENTIFIER"  # RDS console -> Databases -> "DB identifier"
$SshHost    = "rapex-ec2"                     # alias from ~/.ssh/config

$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }

Write-Host "Starting EC2 instance $InstanceId..."
& $aws ec2 start-instances --region $Region --instance-ids $InstanceId --output table

if ($RdsId -ne "REPLACE_WITH_YOUR_RDS_DB_IDENTIFIER") {
    Write-Host "`nStarting RDS instance $RdsId..."
    try {
        & $aws rds start-db-instance --region $Region --db-instance-identifier $RdsId --output table
    } catch {
        Write-Host "RDS start request failed (it may already be running): $_" -ForegroundColor Yellow
    }
} else {
    Write-Host "`nSkipping RDS -- edit this script and set `$RdsId to your actual DB identifier first." -ForegroundColor Yellow
}

Write-Host "`nWaiting for EC2 status checks to pass (this can take 1-2 min)..."
& $aws ec2 wait instance-status-ok --region $Region --instance-ids $InstanceId

Write-Host "Instance is up. Waiting for SSH to accept connections..."
$ready = $false
for ($i = 0; $i -lt 24; $i++) {
    $result = ssh -o ConnectTimeout=5 -o BatchMode=yes $SshHost "echo ok" 2>$null
    if ($result -eq "ok") { $ready = $true; break }
    Start-Sleep -Seconds 5
}

if ($ready) {
    Write-Host "`nEC2 is up and reachable over SSH." -ForegroundColor Green
    Write-Host "The 'throughline' service is enabled and starts automatically on boot -- no need to restart it manually unless you're deploying new code."
    Write-Host "Run scripts\ec2-deploy.ps1 next to push your latest local changes and restart the service."
} else {
    Write-Host "`nInstance booted but SSH never became reachable after 2 minutes -- check the AWS console (security group, instance status) before deploying." -ForegroundColor Red
}
