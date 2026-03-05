# V1 Legacy 리소스 정리 — SSOT에 없는 리소스만 대상. 삭제 전 reports 기록 필수.
# AWS·Cloudflare(클플) 인증: Cursor 룰에 따라 .env를 환경변수로 주입 후 실행 (run-with-env.ps1 권장).
# 기능: orphan EIP release, unused SG delete, build server stop, legacy EC2 terminate.
# 사용: -DryRun (기본, 변경 없음) / -Execute (실제 적용). -RefreshReports 이면 먼저 인벤토리·정리계획 갱신.
param(
    [switch]$DryRun = $true,
    [switch]$Execute,
    [switch]$RefreshReports,
    [switch]$EIPOnly,
    [switch]$SGOnly,
    [switch]$BuildStopOnly,
    [switch]$EC2Only
)
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$ReportsDir = Join-Path $RepoRoot "docs\00-SSOT\v1\reports"
$InventoryPath = Join-Path $ReportsDir "aws-resource-inventory.latest.md"
$PlanPath = Join-Path $ReportsDir "resource-cleanup-plan.latest.md"

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\env.ps1")
$null = Load-SSOT -Env "prod"
$R = $script:Region
$VpcId = $script:VpcId
if (-not $VpcId) { Write-Error "SSOT VpcId not set" }

if ($Execute) { $DryRun = $false }

# 삭제 전 보고서 존재 확인 (절대 규칙)
function Test-ReportsExist {
    if (-not (Test-Path $InventoryPath)) {
        Write-Error "보고서 없음. 먼저 run-resource-inventory.ps1 실행: pwsh -File scripts/v1/run-with-env.ps1 -- pwsh -File scripts/v1/run-resource-inventory.ps1"
    }
    if (-not (Test-Path $PlanPath)) {
        Write-Error "정리 계획 없음. 먼저 run-resource-inventory.ps1 실행하여 resource-cleanup-plan.latest.md 생성."
    }
}

if ($RefreshReports) {
    Write-Host "  인벤토리·정리계획 갱신 중..." -ForegroundColor Cyan
    & (Join-Path $ScriptRoot "run-resource-inventory.ps1") | Out-Host
}

if (-not $DryRun -and $Execute) {
    Test-ReportsExist
}

$KeepASG = @($script:ApiASGName, $script:MessagingASGName, $script:AiASGName)
$KeepBatchCE = @($script:VideoCEName, $script:VideoLongCEName, $script:OpsCEName) | Where-Object { $_ -and $_.Trim() -ne "" }
$BatchOpsASGPrefix = "academy-v1-video-ops-ce-asg-"
$BuildTagKey = $script:BuildTagKey
$BuildTagValue = $script:BuildTagValue
$KeepSGNames = @("academy-v1-sg-app", "academy-v1-sg-batch", "academy-v1-sg-data", "default")
$KeepSGIds = @($script:SecurityGroupApp, $script:BatchSecurityGroupId, $script:SecurityGroupData) | Where-Object { $_ -and $_.Trim() -ne "" }

# NAT EIP 제외용
function Get-NatAllocationId {
    $natRes = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$VpcId", "Name=state,Values=available", "--region", $R, "--output", "json")
    if ($natRes -and $natRes.NatGateways -and $natRes.NatGateways.Count -gt 0 -and $natRes.NatGateways[0].NatGatewayAddresses.Count -gt 0) {
        return $natRes.NatGateways[0].NatGatewayAddresses[0].AllocationId
    }
    return $null
}

# Used instance IDs (SSOT 유지 인스턴스)
function Get-UsedInstanceIds {
    $used = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $asgRes = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
    if ($asgRes -and $asgRes.AutoScalingGroups) {
        foreach ($asg in $asgRes.AutoScalingGroups) {
            $keep = $asg.AutoScalingGroupName -in $KeepASG -or $asg.AutoScalingGroupName -like "${BatchOpsASGPrefix}*" -or $asg.AutoScalingGroupName -like "*academy-v1-video*"
            if (-not $keep) { continue }
            foreach ($inst in $asg.Instances) { [void]$used.Add($inst.InstanceId) }
        }
    }
    $ceRes = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
    if ($ceRes -and $ceRes.computeEnvironments) {
        foreach ($ce in $ceRes.computeEnvironments) {
            if ($ce.computeEnvironmentName -notin $KeepBatchCE) { continue }
            if ($ce.ecsClusterArn) {
                $clusterName = $ce.ecsClusterArn -replace '^.*/', ''
                $ciRes = Invoke-AwsJson @("ecs", "list-container-instances", "--cluster", $clusterName, "--region", $R, "--output", "json")
                if ($ciRes -and $ciRes.containerInstanceArns -and $ciRes.containerInstanceArns.Count -gt 0) {
                    $desc = Invoke-AwsJson @("ecs", "describe-container-instances", "--cluster", $clusterName, "--container-instances", ($ciRes.containerInstanceArns -join ","), "--region", $R, "--output", "json")
                    if ($desc -and $desc.containerInstances) {
                        foreach ($ci in $desc.containerInstances) { if ($ci.ec2InstanceId) { [void]$used.Add($ci.ec2InstanceId) } }
                    }
                }
            }
        }
    }
    $buildRes = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=tag:$BuildTagKey,Values=$BuildTagValue", "Name=instance-state-name,Values=running,pending,stopped", "--region", $R, "--output", "json")
    if ($buildRes -and $buildRes.Reservations) {
        foreach ($rev in $buildRes.Reservations) { foreach ($i in $rev.Instances) { [void]$used.Add($i.InstanceId) } }
    }
    return [string[]]@($used)
}

Write-Host "`n=== V1 Legacy 정리 (DryRun=$DryRun) ===" -ForegroundColor Cyan
Write-Host "  Region: $R  VpcId: $VpcId" -ForegroundColor Gray
if ($DryRun) { Write-Host "  모드: DryRun — 변경 없음. 실제 적용 시 -Execute 사용." -ForegroundColor Yellow }

# 1) Orphan EIP release
if (-not $EIPOnly -and -not $SGOnly -and -not $BuildStopOnly -and -not $EC2Only) { $doEIP = $true } else { $doEIP = $EIPOnly }
if ($doEIP) {
    $natAlloc = Get-NatAllocationId
    $addrs = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
    $toRelease = @($addrs.Addresses | Where-Object { ($null -eq $_.AssociationId) -and ($null -eq $natAlloc -or $_.AllocationId -ne $natAlloc) })
    if ($toRelease.Count -eq 0) { Write-Host "  EIP: 정리 대상 없음 (NAT EIP 유지)." -ForegroundColor Green }
    else {
        Write-Host "  EIP: $($toRelease.Count) 개 미연결 → release" -ForegroundColor $(if ($DryRun) { "Yellow" } else { "Red" })
        foreach ($a in $toRelease) {
            Write-Host "    - $($a.AllocationId) $($a.PublicIp)" -ForegroundColor Gray
            if (-not $DryRun) {
                try {
                    Invoke-Aws @("ec2", "release-address", "--allocation-id", $a.AllocationId, "--region", $R) -ErrorMessage "release-address" | Out-Null
                    Write-Host "      Released." -ForegroundColor Green
                } catch { Write-Warning "      Failed: $_" }
            }
        }
    }
}

# 2) Unused SG delete (ENI 0개, SSOT keep 아님)
if (-not $EIPOnly -and -not $SGOnly -and -not $BuildStopOnly -and -not $EC2Only) { $doSG = $true } else { $doSG = $SGOnly }
if ($doSG -and $VpcId) {
    $sgRes = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $R, "--output", "json")
    $toDelete = [System.Collections.ArrayList]::new()
    foreach ($sg in $sgRes.SecurityGroups) {
        if ($sg.GroupName -in $KeepSGNames -or $sg.GroupId -in $KeepSGIds) { continue }
        $eniRes = Invoke-AwsJson @("ec2", "describe-network-interfaces", "--filters", "Name=group-id,Values=$($sg.GroupId)", "--region", $R, "--output", "json")
        $eniCount = if ($eniRes -and $eniRes.NetworkInterfaces) { $eniRes.NetworkInterfaces.Count } else { 0 }
        if ($eniCount -eq 0) { [void]$toDelete.Add($sg) }
    }
    if ($toDelete.Count -eq 0) { Write-Host "  SG: 사용 중이 아닌 SG 없음." -ForegroundColor Green }
    else {
        Write-Host "  SG: $($toDelete.Count) 개 (ENI 없음) → delete" -ForegroundColor $(if ($DryRun) { "Yellow" } else { "Red" })
        foreach ($sg in $toDelete) {
            Write-Host "    - $($sg.GroupId) $($sg.GroupName)" -ForegroundColor Gray
            if (-not $DryRun) {
                try {
                    Invoke-Aws @("ec2", "delete-security-group", "--group-id", $sg.GroupId, "--region", $R) -ErrorMessage "delete-security-group" | Out-Null
                    Write-Host "      Deleted." -ForegroundColor Green
                } catch { Write-Warning "      Failed: $_" }
            }
        }
    }
}

# 3) Build server stop (academy-build-arm64)
if (-not $EIPOnly -and -not $SGOnly -and -not $BuildStopOnly -and -not $EC2Only) { $doBuild = $true } else { $doBuild = $BuildStopOnly }
if ($doBuild) {
    $buildRes = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=tag:$BuildTagKey,Values=$BuildTagValue", "Name=instance-state-name,Values=running", "--region", $R, "--output", "json")
    $running = @()
    if ($buildRes -and $buildRes.Reservations) {
        foreach ($rev in $buildRes.Reservations) { foreach ($i in $rev.Instances) { if ($i.State.Name -eq "running") { $running += $i } } }
    }
    if ($running.Count -eq 0) { Write-Host "  Build 서버: 실행 중인 인스턴스 없음." -ForegroundColor Green }
    else {
        Write-Host "  Build 서버: $($running.Count) 개 → stop" -ForegroundColor $(if ($DryRun) { "Yellow" } else { "Red" })
        foreach ($i in $running) {
            $name = ($i.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
            Write-Host "    - $($i.InstanceId) ($name)" -ForegroundColor Gray
            if (-not $DryRun) {
                try {
                    Invoke-Aws @("ec2", "stop-instances", "--instance-ids", $i.InstanceId, "--region", $R) -ErrorMessage "stop-instances" | Out-Null
                    Write-Host "      Stop 요청됨." -ForegroundColor Green
                } catch { Write-Warning "      Failed: $_" }
            }
        }
    }
}

# 4) Legacy EC2 terminate (유지 ASG/Build/Batch CE 제외)
if (-not $EIPOnly -and -not $SGOnly -and -not $BuildStopOnly -and -not $EC2Only) { $doEC2 = $true } else { $doEC2 = $EC2Only }
if ($doEC2 -and $VpcId) {
    $usedIds = Get-UsedInstanceIds
    $allRes = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=vpc-id,Values=$VpcId", "Name=instance-state-name,Values=running,pending,stopped", "--region", $R, "--output", "json")
    $toTerminate = [System.Collections.ArrayList]::new()
    foreach ($rev in $allRes.Reservations) {
        foreach ($i in $rev.Instances) {
            if ($usedIds -contains $i.InstanceId) { continue }
            $name = ($i.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
            [void]$toTerminate.Add([PSCustomObject]@{ InstanceId = $i.InstanceId; Name = $name; State = $i.State.Name })
        }
    }
    if ($toTerminate.Count -eq 0) { Write-Host "  EC2: 정리 대상 오프안 인스턴스 없음." -ForegroundColor Green }
    else {
        Write-Host "  EC2: $($toTerminate.Count) 개 오프안 → terminate" -ForegroundColor $(if ($DryRun) { "Yellow" } else { "Red" })
        foreach ($o in $toTerminate) {
            Write-Host "    - $($o.InstanceId) ($($o.Name)) $($o.State)" -ForegroundColor Gray
            if (-not $DryRun) {
                try {
                    Invoke-Aws @("ec2", "terminate-instances", "--instance-ids", $o.InstanceId, "--region", $R) -ErrorMessage "terminate-instances" | Out-Null
                    Write-Host "      Terminate 요청됨." -ForegroundColor Green
                } catch { Write-Warning "      Failed: $_" }
            }
        }
    }
}

Write-Host "`n=== 완료 ===`n" -ForegroundColor Cyan
