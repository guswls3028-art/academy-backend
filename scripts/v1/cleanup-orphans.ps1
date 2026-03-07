# cleanup-orphans.ps1 — Safely remove orphan AWS resources
# Region: ap-northeast-2
# Run: pwsh scripts/v1/cleanup-orphans.ps1 [-DryRun] [-Execute]
# AWS auth: Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION or use -AwsProfile

param(
    [switch]$DryRun = $true,
    [switch]$Execute = $false,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$R = "ap-northeast-2"

if ($AwsProfile -and $AwsProfile -ne "") {
    $env:AWS_PROFILE = $AwsProfile
}
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = $R }

function Invoke-Aws {
    param([string[]]$CmdArgs)
    $out = & aws $CmdArgs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "AWS CLI failed: $out" }
    return $out
}

function Invoke-AwsJson {
    param([string[]]$CmdArgs)
    $out = Invoke-Aws ($CmdArgs + @("--output", "json"))
    return $out | ConvertFrom-Json
}

$doApply = $Execute -and -not $DryRun

Write-Host "`n=== CLEANUP ORPHANS (ap-northeast-2) ===" -ForegroundColor Cyan
Write-Host "Mode: $(if ($doApply) { 'EXECUTE' } else { 'DRY RUN (no changes)' })" -ForegroundColor $(if ($doApply) { 'Yellow' } else { 'Green' })
Write-Host ""

# 1) Orphan ENI (available, not attached)
Write-Host "[1] Orphan ENIs (status=available)" -ForegroundColor Cyan
try {
    $filterArgs = @(
        "ec2", "describe-network-interfaces", "--region", $R,
        "--filters", "Name=status,Values=available", "Name=vpc-id,Values=vpc-0831a2484f9b114c2"
    )
    $enis = Invoke-AwsJson $filterArgs
    if ($enis.NetworkInterfaces -and $enis.NetworkInterfaces.Count -gt 0) {
        foreach ($eni in $enis.NetworkInterfaces) {
            Write-Host "  ENI $($eni.NetworkInterfaceId) - $($eni.Description)" -ForegroundColor Gray
            if ($doApply) {
                Invoke-Aws @("ec2", "delete-network-interface", "--network-interface-id", $eni.NetworkInterfaceId, "--region", $R)
                Write-Host "    Deleted: $($eni.NetworkInterfaceId)" -ForegroundColor Green
            }
        }
    } else {
        Write-Host "  None found" -ForegroundColor DarkGray
    }
} catch { Write-Warning "  $_" }

# 2) Orphan Security Groups (0 ENI, SSOT 외 legacy 이름)
$keepSgNames = @("academy-v1-sg-app", "academy-v1-sg-batch", "academy-v1-sg-data", "default", "academy-rds", "academy-redis-sg")
$legacySgNames = @("academy-api-sg", "academy-worker-sg")
$vpcId = "vpc-0831a2484f9b114c2"
$allSgs = (Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$vpcId", "--region", $R)).SecurityGroups
$orphanSgs = @()
foreach ($sg in $allSgs) {
    if ($sg.GroupName -in $keepSgNames) { continue }
    if ($sg.GroupName -in $legacySgNames) {
        $eniRes = Invoke-AwsJson @("ec2", "describe-network-interfaces", "--region", $R, "--filters", "Name=group-id,Values=$($sg.GroupId)")
        $eniCount = if ($eniRes -and $eniRes.NetworkInterfaces) { $eniRes.NetworkInterfaces.Count } else { 0 }
        if ($eniCount -eq 0) { $orphanSgs += @{ Id = $sg.GroupId; Name = $sg.GroupName } }
    }
}
# academy-v1-vpce-sg: 0 ENI인 경우만 (중복/미사용). 4 ENI 있으면 스킵
$vpceSg = $allSgs | Where-Object { $_.GroupName -eq "academy-v1-vpce-sg" } | Select-Object -First 1
if ($vpceSg) {
    $vpceEniRes = Invoke-AwsJson @("ec2", "describe-network-interfaces", "--region", $R, "--filters", "Name=group-id,Values=$($vpceSg.GroupId)")
    $vpceEni = if ($vpceEniRes -and $vpceEniRes.NetworkInterfaces) { $vpceEniRes.NetworkInterfaces.Count } else { 0 }
    if ($vpceEni -eq 0) { $orphanSgs += @{ Id = $vpceSg.GroupId; Name = $vpceSg.GroupName } }
}

Write-Host "`n[2] Orphan Security Groups (0 ENI)" -ForegroundColor Cyan
if ($orphanSgs.Count -eq 0) { Write-Host "  None found" -ForegroundColor DarkGray }
foreach ($sg in $orphanSgs) {
    try {
        Write-Host "  $($sg.Name) ($($sg.Id)) - 0 ENI" -ForegroundColor Gray
        if ($doApply) {
            Invoke-Aws @("ec2", "delete-security-group", "--group-id", $sg.Id, "--region", $R)
            Write-Host "    Deleted: $($sg.Name)" -ForegroundColor Green
        }
    } catch { Write-Warning "  $($sg.Name): $_" }
}

# 3) Legacy EventBridge rules (DISABLED)
$legacyRules = @(
    "academy-reconcile-video-jobs",
    "academy-video-scan-stuck-rate",
    "academy-worker-autoscale-rate",
    "academy-worker-queue-depth-rate"
)

Write-Host "`n[3] Legacy EventBridge Rules (DISABLED)" -ForegroundColor Cyan
foreach ($ruleName in $legacyRules) {
    try {
        $rule = Invoke-AwsJson @("events", "describe-rule", "--name", $ruleName, "--region", $R) 2>$null
        if ($rule) {
            Write-Host "  $ruleName (State: $($rule.State))" -ForegroundColor Gray
            if ($doApply) {
                $targets = Invoke-AwsJson @("events", "list-targets-by-rule", "--rule", $ruleName, "--region", $R)
                if ($targets.Targets -and $targets.Targets.Count -gt 0) {
                    $ids = $targets.Targets | ForEach-Object { $_.Id }
                    $args = @("events", "remove-targets", "--rule", $ruleName, "--ids") + [string[]]$ids + @("--region", $R)
                    Invoke-Aws $args
                }
                Invoke-Aws @("events", "delete-rule", "--name", $ruleName, "--region", $R)
                Write-Host "    Deleted: $ruleName" -ForegroundColor Green
            }
        }
    } catch { Write-Host "  $ruleName - not found or error: $_" -ForegroundColor DarkGray }
}

# 4) Unattached EIPs (optional)
Write-Host "`n[4] Unattached EIPs" -ForegroundColor Cyan
try {
    $addrs = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R)
    $orphan = $addrs.Addresses | Where-Object { -not $_.AssociationId }
    if ($orphan -and $orphan.Count -gt 0) {
        foreach ($eip in $orphan) {
            Write-Host "  $($eip.AllocationId) - $($eip.PublicIp)" -ForegroundColor Gray
            if ($doApply) {
                Invoke-Aws @("ec2", "release-address", "--allocation-id", $eip.AllocationId, "--region", $R)
                Write-Host "    Released: $($eip.AllocationId)" -ForegroundColor Green
            }
        }
    } else {
        Write-Host "  None found" -ForegroundColor DarkGray
    }
} catch { Write-Warning "  $_" }

Write-Host "`n=== CLEANUP COMPLETE ===" -ForegroundColor Cyan
if (-not $doApply) {
    Write-Host "To apply changes, run: pwsh scripts/v1/cleanup-orphans.ps1 -Execute" -ForegroundColor Yellow
}
