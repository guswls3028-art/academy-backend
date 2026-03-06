# V1 불필요 리소스 정리 — 돈 새는 리소스 우선. 배포 수정 전 실행 권장.
# PHASE 1: EIP 전부 release (association 없음)
# PHASE 2: ENI에 연결되지 않은 Security Group 삭제 (SSOT 유지 SG 제외)
# PHASE 3: API ASG 축소 (min=1 desired=1 max=2)
# PHASE 4: describe-* 로 재검증 후 docs/00-SSOT/v1/reports/resource-cleanup.latest.md 기록
# 사용: pwsh -File scripts/v1/run-resource-cleanup.ps1 [-AwsProfile default] [-Execute]
param(
    [string]$AwsProfile = "",
    [switch]$Execute
)
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$ReportsDir = Join-Path $RepoRoot "docs\00-SSOT\v1\reports"
$ResourceCleanupPath = Join-Path $ReportsDir "resource-cleanup.latest.md"

. (Join-Path $ScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
    Write-Host "Using AWS_PROFILE: $env:AWS_PROFILE" -ForegroundColor Gray
}

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env prod
$R = $script:Region
$VpcId = $script:VpcId
$script:PlanMode = $false

$KeepSGNames = @("academy-v1-sg-app", "academy-v1-sg-batch", "academy-v1-sg-data", "default")
$KeepSGIds = @($script:SecurityGroupApp, $script:BatchSecurityGroupId, $script:SecurityGroupData) | Where-Object { $_ -and $_.Trim() -ne "" }
$ApiASGName = $script:ApiASGName

function Get-NatAllocationId {
    if (-not $VpcId) { return $null }
    $natRes = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$VpcId", "Name=state,Values=available", "--region", $R, "--output", "json")
    if ($natRes -and $natRes.NatGateways -and $natRes.NatGateways.Count -gt 0 -and $natRes.NatGateways[0].NatGatewayAddresses.Count -gt 0) {
        return $natRes.NatGateways[0].NatGatewayAddresses[0].AllocationId
    }
    return $null
}

Write-Host "`n=== V1 리소스 정리 (Execute=$Execute) ===" -ForegroundColor Cyan
Write-Host "  Region: $R  VpcId: $VpcId" -ForegroundColor Gray
if (-not $Execute) { Write-Host "  모드: DryRun — 변경 없음. 실제 적용 시 -Execute 사용." -ForegroundColor Yellow }

# --- PHASE 1: Elastic IP 제거 ---
Write-Host "`n[PHASE 1] Elastic IP" -ForegroundColor Cyan
$natAlloc = Get-NatAllocationId
$addrs = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
$toRelease = @($addrs.Addresses | Where-Object { ($null -eq $_.AssociationId) -and ($null -eq $natAlloc -or $_.AllocationId -ne $natAlloc) })
if ($toRelease.Count -eq 0) { Write-Host "  정리 대상 없음 (NAT EIP 유지)." -ForegroundColor Green }
else {
    Write-Host "  미연결 $($toRelease.Count) 개 → release" -ForegroundColor $(if ($Execute) { "Yellow" } else { "Gray" })
    foreach ($a in $toRelease) {
        Write-Host "    - $($a.AllocationId) $($a.PublicIp)" -ForegroundColor Gray
        if ($Execute) {
            try {
                Invoke-Aws @("ec2", "release-address", "--allocation-id", $a.AllocationId, "--region", $R) -ErrorMessage "release-address" | Out-Null
                Write-Host "      Released." -ForegroundColor Green
            } catch { Write-Warning "      Failed: $_" }
        }
    }
}

# --- PHASE 2: Security Group 정리 ---
Write-Host "`n[PHASE 2] Security Group" -ForegroundColor Cyan
if (-not $VpcId) { Write-Host "  VpcId 없음, 건너뜀." -ForegroundColor Yellow }
else {
    $sgRes = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $R, "--output", "json")
    $toDelete = [System.Collections.ArrayList]::new()
    foreach ($sg in $sgRes.SecurityGroups) {
        if ($sg.GroupName -in $KeepSGNames -or $sg.GroupId -in $KeepSGIds) { continue }
        $eniRes = Invoke-AwsJson @("ec2", "describe-network-interfaces", "--filters", "Name=group-id,Values=$($sg.GroupId)", "--region", $R, "--output", "json")
        $eniCount = if ($eniRes -and $eniRes.NetworkInterfaces) { $eniRes.NetworkInterfaces.Count } else { 0 }
        if ($eniCount -eq 0) { [void]$toDelete.Add($sg) }
    }
    if ($toDelete.Count -eq 0) { Write-Host "  ENI 없는 SG 없음." -ForegroundColor Green }
    else {
        Write-Host "  ENI 없음 $($toDelete.Count) 개 → delete" -ForegroundColor $(if ($Execute) { "Yellow" } else { "Gray" })
        foreach ($sg in $toDelete) {
            Write-Host "    - $($sg.GroupId) $($sg.GroupName)" -ForegroundColor Gray
            if ($Execute) {
                try {
                    Invoke-Aws @("ec2", "delete-security-group", "--group-id", $sg.GroupId, "--region", $R) -ErrorMessage "delete-security-group" | Out-Null
                    Write-Host "      Deleted." -ForegroundColor Green
                } catch { Write-Warning "      Failed: $_" }
            }
        }
    }
}

# --- PHASE 3: API ASG 축소 ---
Write-Host "`n[PHASE 3] API ASG 축소 (min=1 desired=1 max=2)" -ForegroundColor Cyan
$asgDesc = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $ApiASGName, "--region", $R, "--output", "json")
if (-not $asgDesc -or -not $asgDesc.AutoScalingGroups -or $asgDesc.AutoScalingGroups.Count -eq 0) {
    Write-Host "  $ApiASGName 없음, 건너뜀." -ForegroundColor Yellow
} else {
    $a = $asgDesc.AutoScalingGroups[0]
    $min = $a.MinSize; $des = $a.DesiredCapacity; $max = $a.MaxSize
    if ($min -eq 1 -and $des -eq 1 -and $max -eq 2) { Write-Host "  이미 1/1/2." -ForegroundColor Green }
    else {
        Write-Host "  현재 min=$min desired=$des max=$max → 1/1/2" -ForegroundColor $(if ($Execute) { "Yellow" } else { "Gray" })
        if ($Execute) {
            try {
                Invoke-Aws @("autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $ApiASGName, "--min-size", "1", "--desired-capacity", "1", "--max-size", "2", "--region", $R) -ErrorMessage "update-asg" | Out-Null
                Write-Host "      Updated." -ForegroundColor Green
            } catch { Write-Warning "      Failed: $_" }
        }
    }
}

# --- PHASE 4: 리소스 재검증 → resource-cleanup.latest.md ---
Write-Host "`n[PHASE 4] 리소스 재검증 및 보고서 기록" -ForegroundColor Cyan
$runAt = Get-Date -Format "o"

$instRes = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=instance-state-name,Values=running", "Name=tag:Project,Values=academy", "--region", $R, "--output", "json")
$runningCount = 0
$instanceRows = [System.Collections.ArrayList]::new()
if ($instRes -and $instRes.Reservations) {
    foreach ($rev in $instRes.Reservations) {
        foreach ($i in $rev.Instances) {
            if ($i) { $runningCount++; $name = ($i.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value; [void]$instanceRows.Add([PSCustomObject]@{ Id = $i.InstanceId; Name = $name; Type = $i.InstanceType }) }
        }
    }
}

$sgRes = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $R, "--output", "json")
$sgCount = 0
$sgRows = [System.Collections.ArrayList]::new()
if ($sgRes -and $sgRes.SecurityGroups) {
    $sgCount = $sgRes.SecurityGroups.Count
    foreach ($sg in $sgRes.SecurityGroups) { [void]$sgRows.Add([PSCustomObject]@{ GroupId = $sg.GroupId; GroupName = $sg.GroupName }) }
}

$addrRes = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
$eipCount = 0
$eipRows = [System.Collections.ArrayList]::new()
if ($addrRes -and $addrRes.Addresses) {
    $eipCount = $addrRes.Addresses.Count
    foreach ($a in $addrRes.Addresses) { [void]$eipRows.Add([PSCustomObject]@{ AllocationId = $a.AllocationId; PublicIp = $a.PublicIp; Associated = ($null -ne $a.InstanceId -or $null -ne $a.NetworkInterfaceId) }) }
}

$asgRes = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
$asgRows = [System.Collections.ArrayList]::new()
if ($asgRes -and $asgRes.AutoScalingGroups) {
    foreach ($asg in $asgRes.AutoScalingGroups) {
        if ($asg.AutoScalingGroupName -like "academy*" -or $asg.AutoScalingGroupName -like "*v1*") {
            [void]$asgRows.Add([PSCustomObject]@{ Name = $asg.AutoScalingGroupName; Min = $asg.MinSize; Desired = $asg.DesiredCapacity; Max = $asg.MaxSize })
        }
    }
}

$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# V1 리소스 정리·재검증 결과")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**리전:** $R **갱신:** $runAt **모드:** $(if ($Execute) { 'Execute' } else { 'DryRun' })")
[void]$sb.AppendLine("**SSOT:** docs/00-SSOT/v1/params.yaml")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 요약")
[void]$sb.AppendLine("| 항목 | 값 | 목표(V1 정상) |")
[void]$sb.AppendLine("|------|-----|----------------|")
[void]$sb.AppendLine("| running instances | $runningCount | 3 |")
[void]$sb.AppendLine("| Security Groups (VPC) | $sgCount | 6~8 |")
[void]$sb.AppendLine("| Elastic IP | $eipCount | 0 |")
[void]$sb.AppendLine("| ASG (academy/v1) | $($asgRows.Count) | 3 + Batch ops |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Running instances (Project=academy)")
[void]$sb.AppendLine("| InstanceId | Name | Type |")
[void]$sb.AppendLine("|------------|------|------|")
foreach ($r in $instanceRows) { [void]$sb.AppendLine("| $($r.Id) | $($r.Name) | $($r.Type) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Security Groups (VPC)")
[void]$sb.AppendLine("| GroupId | GroupName |")
[void]$sb.AppendLine("|---------|-----------|")
foreach ($r in $sgRows) { [void]$sb.AppendLine("| $($r.GroupId) | $($r.GroupName) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Elastic IP")
[void]$sb.AppendLine("| AllocationId | PublicIp | Associated |")
[void]$sb.AppendLine("|--------------|----------|------------|")
foreach ($r in $eipRows) { [void]$sb.AppendLine("| $($r.AllocationId) | $($r.PublicIp) | $($r.Associated) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## ASG (academy/v1)")
[void]$sb.AppendLine("| Name | Min | Desired | Max |")
[void]$sb.AppendLine("|------|-----|---------|-----|")
foreach ($r in $asgRows) { [void]$sb.AppendLine("| $($r.Name) | $($r.Min) | $($r.Desired) | $($r.Max) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("실행: ``pwsh -File scripts/v1/run-resource-cleanup.ps1 -AwsProfile default -Execute``")

if (-not (Test-Path $ReportsDir)) { New-Item -ItemType Directory -Path $ReportsDir -Force | Out-Null }
Set-Content -Path $ResourceCleanupPath -Value $sb.ToString() -Encoding UTF8 -Force
Write-Host "  resource-cleanup.latest.md: $ResourceCleanupPath" -ForegroundColor Green

Write-Host "`n=== 완료 ===" -ForegroundColor Cyan
Write-Host "  Instances: $runningCount  SG: $sgCount  EIP: $eipCount  ASG: $($asgRows.Count)" -ForegroundColor Gray
Write-Host ""
