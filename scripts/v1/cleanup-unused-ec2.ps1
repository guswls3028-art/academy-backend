# 미사용 EC2 리소스 정리 — academy v1에서 쓰는 것만 남기고 나머지 제거.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다. 호출 전에 에이전트가 .env를 환경변수로 설정한 뒤 실행.
# 사용: -DryRun (기본) 시 삭제 없이 후보만 출력. 실제 삭제: -Execute
param(
    [switch]$DryRun,
    [switch]$Execute,
    [switch]$EIPOnly,
    [switch]$InstancesOnly,
    [switch]$RemoveUnusedSGs
)
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"
# 기본: DryRun. -Execute 이면 실제 삭제
if (-not $PSBoundParameters.ContainsKey('DryRun')) { $DryRun = $true }
if ($Execute) { $DryRun = $false }

# AWS 자격 증명 검증 (호출자가 이미 설정한 환경변수 사용)
. (Join-Path $ScriptRoot "core\env.ps1")
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$null = Assert-AwsCredentials -RepoRoot $RepoRoot

$R = $script:Region
$VpcId = $script:VpcId
if (-not $VpcId) { Write-Error "VpcId not set (params.yaml network.vpcId)" }

# v1에서 유지하는 ASG 이름 패턴
$KeepASGNames = @(
    $script:ApiASGName,
    $script:MessagingASGName,
    $script:AiASGName
)
# Batch Ops CE가 만드는 ASG (이름이 academy-v1-video-ops-ce-asg- 로 시작)
$BatchOpsASGPrefix = "academy-v1-video-ops-ce-asg-"

function Get-UsedInstanceIds {
    $used = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $asgList = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
    if ($asgList -and $asgList.AutoScalingGroups) {
        foreach ($asg in $asgList.AutoScalingGroups) {
            $name = $asg.AutoScalingGroupName
            $keep = ($name -in $KeepASGNames) -or ($name -like "${BatchOpsASGPrefix}*")
            if (-not $keep) { continue }
            foreach ($inst in $asg.Instances) {
                [void]$used.Add($inst.InstanceId)
            }
        }
    }
    # Build server DEPRECATED: GitHub Actions only (no build EC2)
    return [string[]]@($used)
}

function Get-NatAllocationId {
    $r = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$VpcId", "Name=state,Values=available", "--region", $R, "--output", "json")
    if ($r -and $r.NatGateways -and $r.NatGateways.Count -gt 0 -and $r.NatGateways[0].NatGatewayAddresses.Count -gt 0) {
        return $r.NatGateways[0].NatGatewayAddresses[0].AllocationId
    }
    return $null
}

function Remove-UnusedEIPs {
    $natAlloc = Get-NatAllocationId
    $addrs = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
    $toRelease = [System.Collections.ArrayList]::new()
    foreach ($a in $addrs.Addresses) {
        $allocId = $a.AllocationId
        if ($natAlloc -and $allocId -eq $natAlloc) { continue }
        if ($a.AssociationId) { continue }
        [void]$toRelease.Add($a)
    }
    if ($toRelease.Count -eq 0) {
        Write-Host "  EIP: no unused addresses to release." -ForegroundColor Green
        return
    }
    Write-Host "  EIP: $($toRelease.Count) unused (will release unless DryRun)" -ForegroundColor Yellow
    foreach ($a in $toRelease) {
        Write-Host "    - $($a.AllocationId) $($a.PublicIp)" -ForegroundColor Gray
        if ($Execute -and -not $DryRun) {
            try {
                Invoke-Aws @("ec2", "release-address", "--allocation-id", $a.AllocationId, "--region", $R) -ErrorMessage "release-address" | Out-Null
                Write-Host "      Released." -ForegroundColor Green
            } catch {
                Write-Warning "      Failed: $_"
            }
        }
    }
}

function Remove-OrphanInstances {
    $usedIds = Get-UsedInstanceIds
    $all = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=vpc-id,Values=$VpcId", "Name=instance-state-name,Values=running,pending,stopped", "--region", $R, "--output", "json")
    $toTerminate = [System.Collections.ArrayList]::new()
    foreach ($rev in $all.Reservations) {
        foreach ($i in $rev.Instances) {
            $id = $i.InstanceId
            if ($usedIds -contains $id) { continue }
            [void]$toTerminate.Add($i)
        }
    }
    if ($toTerminate.Count -eq 0) {
        Write-Host "  Instances: no orphan instances in VPC $VpcId" -ForegroundColor Green
        return
    }
    Write-Host "  Instances: $($toTerminate.Count) orphan(s) (not in v1 ASGs)" -ForegroundColor Yellow
    foreach ($i in $toTerminate) {
        $name = ($i.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
        Write-Host "    - $($i.InstanceId) $($i.State.Name) Name=$name" -ForegroundColor Gray
        if ($Execute -and -not $DryRun) {
            try {
                Invoke-Aws @("ec2", "terminate-instances", "--instance-ids", $i.InstanceId, "--region", $R) -ErrorMessage "terminate-instances" | Out-Null
                Write-Host "      Terminate requested." -ForegroundColor Green
            } catch {
                Write-Warning "      Failed: $_"
            }
        }
    }
}

function Remove-UnusedSecurityGroups {
    $keepSgIds = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $keepNames = @("academy-v1-sg-app", "academy-v1-sg-batch", "academy-v1-sg-data", "default")
    $sgList = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $R, "--output", "json")
    foreach ($sg in $sgList.SecurityGroups) {
        if ($sg.GroupName -in $keepNames) { [void]$keepSgIds.Add($sg.GroupId); continue }
        if ($sg.GroupId -eq $script:SecurityGroupApp) { [void]$keepSgIds.Add($sg.GroupId); continue }
        if ($sg.GroupId -eq $script:SecurityGroupData) { [void]$keepSgIds.Add($sg.GroupId); continue }
        if ($sg.GroupId -eq $script:BatchSecurityGroupId) { [void]$keepSgIds.Add($sg.GroupId); continue }
        if ($sg.GroupName -eq "default") { [void]$keepSgIds.Add($sg.GroupId); continue }
    }
    $toDelete = [System.Collections.ArrayList]::new()
    foreach ($sg in $sgList.SecurityGroups) {
        if ($keepSgIds.Contains($sg.GroupId)) { continue }
        $eni = Invoke-AwsJson @("ec2", "describe-network-interfaces", "--filters", "Name=group-id,Values=$($sg.GroupId)", "--region", $R, "--output", "json")
        if ($eni -and $eni.NetworkInterfaces -and $eni.NetworkInterfaces.Count -gt 0) { continue }
        [void]$toDelete.Add($sg)
    }
    if ($toDelete.Count -eq 0) {
        Write-Host "  SecurityGroups: no unused SGs (no ENI) to remove in VPC" -ForegroundColor Green
        return
    }
    Write-Host "  SecurityGroups: $($toDelete.Count) unused (no ENI attached)" -ForegroundColor Yellow
    foreach ($sg in $toDelete) {
        Write-Host "    - $($sg.GroupId) $($sg.GroupName)" -ForegroundColor Gray
        if ($RemoveUnusedSGs -and $Execute -and -not $DryRun) {
            try {
                Invoke-Aws @("ec2", "delete-security-group", "--group-id", $sg.GroupId, "--region", $R) -ErrorMessage "delete-security-group" | Out-Null
                Write-Host "      Deleted." -ForegroundColor Green
            } catch {
                Write-Warning "      Failed: $_"
            }
        }
    }
}

Write-Host "`n=== Cleanup unused EC2 (v1 keep list) ===" -ForegroundColor Cyan
Write-Host "  VpcId: $VpcId  Region: $R" -ForegroundColor Gray
if ($DryRun -and -not $Execute) {
    Write-Host "  Mode: DryRun (no changes). Use -Execute to apply." -ForegroundColor Yellow
} else {
    Write-Host "  Mode: Execute (changes will be applied)" -ForegroundColor Red
}

if (-not $InstancesOnly) { Remove-UnusedEIPs }
if (-not $EIPOnly)       { Remove-OrphanInstances }
if ($RemoveUnusedSGs)    { Remove-UnusedSecurityGroups }

Write-Host "`n=== Done ===`n" -ForegroundColor Cyan
