# Stops the EC2 instance and RDS instance backing Throughline, to avoid
# paying for idle compute between dev/testing sessions. Non-destructive --
# nothing is deleted, storage/data is untouched, just compute billing pauses.
#
# Usage:  powershell -File scripts\ec2-stop.ps1

$ErrorActionPreference = "Stop"

$Region     = "eu-north-1"
$InstanceId = "i-072634a707e093ae3"          # rapex_ec2
$RdsId      = "REPLACE_WITH_YOUR_RDS_DB_IDENTIFIER"  # RDS console -> Databases -> "DB identifier"

$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }

Write-Host "Stopping EC2 instance $InstanceId..."
& $aws ec2 stop-instances --region $Region --instance-ids $InstanceId --output table

if ($RdsId -eq "REPLACE_WITH_YOUR_RDS_DB_IDENTIFIER") {
    Write-Host "`nSkipping RDS -- edit this script and set `$RdsId to your actual DB identifier first." -ForegroundColor Yellow
} else {
    Write-Host "`nStopping RDS instance $RdsId..."
    try {
        & $aws rds stop-db-instance --region $Region --db-instance-identifier $RdsId --output table
    } catch {
        Write-Host "RDS stop request failed (it may already be stopped, or mid-transition): $_" -ForegroundColor Yellow
    }
}

Write-Host "`nStop requests sent. EC2 usually takes ~30-60s, RDS a few minutes."
Write-Host "Note: RDS auto-resumes on its own after 7 days if left stopped that long (AWS limitation) -- re-run this script if you're still not using it."
Write-Host "Note: the Elastic IP stays reserved (small ongoing charge) so your domain keeps pointing at the same address when you start back up."
