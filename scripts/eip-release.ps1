# Releases the EC2 instance's Elastic IP entirely, so it stops billing the
# ~$0.005/hr "public IPv4 address" charge while you're not live. Only run
# this AFTER the instance is stopped (ec2-stop.ps1) -- releasing the IP of a
# RUNNING instance drops it off the internet immediately.
#
# The released IP is gone for good -- go-live.ps1 allocates a NEW one and
# updates DNS to match, so this is safe, just not reversible to the exact
# same address.
#
# Usage:  powershell -File scripts\eip-release.ps1

$ErrorActionPreference = "Stop"

$Region     = "eu-north-1"
$InstanceId = "i-072634a707e093ae3"          # rapex_ec2

$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }

$state = (& $aws ec2 describe-instances --region $Region --instance-ids $InstanceId --query "Reservations[0].Instances[0].State.Name" --output text)
if ($state -ne "stopped") {
    Write-Host "Instance is currently '$state', not 'stopped' -- run ec2-stop.ps1 first. Releasing the IP of a running instance takes it offline immediately." -ForegroundColor Red
    exit 1
}

$addr = & $aws ec2 describe-addresses --region $Region --filters "Name=instance-id,Values=$InstanceId" --query "Addresses[0]" --output json | ConvertFrom-Json
if (-not $addr) {
    Write-Host "No Elastic IP currently associated with $InstanceId -- nothing to release." -ForegroundColor Yellow
    exit 0
}

Write-Host "Disassociating $($addr.PublicIp)..."
& $aws ec2 disassociate-address --region $Region --association-id $addr.AssociationId

Write-Host "Releasing allocation $($addr.AllocationId)..."
& $aws ec2 release-address --region $Region --allocation-id $addr.AllocationId

Write-Host "`nDone. $($addr.PublicIp) is released and no longer billing." -ForegroundColor Green
Write-Host "Note: the domain now points at a dead IP until you run go-live.ps1, which allocates a fresh one and updates DNS."
