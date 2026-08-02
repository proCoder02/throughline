# One-shot "bring the product live" script: starts EC2 (+ RDS), allocates a
# fresh Elastic IP and attaches it, points the domain's Route 53 A record at
# it, fixes up ~/.ssh/config to match the new IP, then deploys latest code
# and health-checks the live domain. Use this after eip-release.ps1 has been
# run (i.e. there's currently no Elastic IP attached).
#
# If you did NOT release the IP (just stopped the instance), don't use this
# -- just run ec2-start.ps1 + ec2-deploy.ps1, the IP never changed.
#
# Usage:  powershell -File scripts\go-live.ps1

$ErrorActionPreference = "Stop"

$Region       = "eu-north-1"
$InstanceId   = "i-072634a707e093ae3"          # rapex_ec2
$Domain       = "rapexapi.nodexdata.click"
$HostedZoneId = "REPLACE_WITH_YOUR_HOSTED_ZONE_ID"   # Route 53 console -> Hosted zones -> click the zone -> Hosted zone ID
$SshConfig    = "$env:USERPROFILE\.ssh\config"
$SshHostAlias = "rapex-ec2"

$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (-not $aws) { $aws = "C:\Program Files\Amazon\AWSCLIV2\aws.exe" }

if ($HostedZoneId -eq "REPLACE_WITH_YOUR_HOSTED_ZONE_ID") {
    Write-Host "Edit this script and set `$HostedZoneId first (Route 53 console -> Hosted zones -> your domain -> Hosted zone ID)." -ForegroundColor Red
    exit 1
}

Write-Host "=== 1. Starting EC2 (+ RDS) ==="
& "$PSScriptRoot\ec2-start.ps1"

Write-Host "`n=== 2. Allocating a fresh Elastic IP ==="
$existing = & $aws ec2 describe-addresses --region $Region --filters "Name=instance-id,Values=$InstanceId" --query "Addresses[0]" --output json | ConvertFrom-Json
if ($existing) {
    Write-Host "Instance already has an Elastic IP ($($existing.PublicIp)) -- skipping allocation, just fixing DNS/SSH config to match." -ForegroundColor Yellow
    $newIp = $existing.PublicIp
} else {
    $alloc = & $aws ec2 allocate-address --region $Region --domain vpc --output json | ConvertFrom-Json
    Write-Host "Allocated $($alloc.PublicIp), associating with $InstanceId..."
    & $aws ec2 associate-address --region $Region --instance-id $InstanceId --allocation-id $alloc.AllocationId | Out-Null
    $newIp = $alloc.PublicIp
}
Write-Host "Instance's public IP: $newIp" -ForegroundColor Green

Write-Host "`n=== 3. Updating Route 53 A record for $Domain ==="
$changeBatch = @{
    Comment = "go-live.ps1 automated update"
    Changes = @(
        @{
            Action = "UPSERT"
            ResourceRecordSet = @{
                Name = $Domain
                Type = "A"
                TTL = 60
                ResourceRecords = @(@{ Value = $newIp })
            }
        }
    )
} | ConvertTo-Json -Depth 10

$tmpFile = [System.IO.Path]::GetTempFileName()
Set-Content -Path $tmpFile -Value $changeBatch -Encoding utf8
$changeId = & $aws route53 change-resource-record-sets --hosted-zone-id $HostedZoneId --change-batch "file://$tmpFile" --query "ChangeInfo.Id" --output text
Remove-Item $tmpFile

Write-Host "Waiting for the DNS change to propagate on Route 53's side..."
& $aws route53 wait resource-record-sets-changed --id $changeId
Write-Host "Route 53 record updated to $newIp." -ForegroundColor Green
Write-Host "Note: your own machine/ISP may still cache the old IP briefly depending on the previous record's TTL -- this only guarantees Route 53 itself is updated." -ForegroundColor Yellow

Write-Host "`n=== 4. Fixing ~/.ssh/config so 'ssh $SshHostAlias' points at the new IP ==="
if (Test-Path $SshConfig) {
    $lines = Get-Content $SshConfig
    $out = @()
    $inBlock = $false
    foreach ($line in $lines) {
        if ($line -match "^\s*Host\s+$SshHostAlias\s*$") { $inBlock = $true; $out += $line; continue }
        if ($inBlock -and $line -match "^\s*Host\s+\S+") { $inBlock = $false }
        if ($inBlock -and $line -match "^\s*HostName\s+") { $out += "  HostName $newIp" }
        else { $out += $line }
    }
    Set-Content -Path $SshConfig -Value $out
    Write-Host "Updated." -ForegroundColor Green
} else {
    Write-Host "No ~/.ssh/config found -- add a Host $SshHostAlias block pointing at $newIp manually." -ForegroundColor Yellow
}

Write-Host "`n=== 5. Deploying latest code ==="
& "$PSScriptRoot\ec2-deploy.ps1"
