# V1 AWS 리소스 인벤토리 수집 및 SSOT 비교 · 정리 계획 생성.
# 리전 ap-northeast-2, SSOT docs/00-SSOT/v1/params.yaml 기준.
# 출력: aws-resource-inventory.latest.md, resource-cleanup-plan.latest.md
# 사용: pwsh -File scripts/v1/run-resource-inventory.ps1 [-AwsProfile default] (run-with-env 권장)
param([string]$AwsProfile = "")
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}
$null = Load-SSOT -Env "prod"
$R = $script:Region
$VpcId = $script:VpcId

# SSOT 유지 리소스 이름 (위 목록 + SSOT 전체)
$KeepASG = @(
    $script:ApiASGName,
    $script:MessagingASGName,
    $script:AiASGName
)
$KeepALB = @($script:ApiAlbName)
$KeepTG = @($script:ApiTargetGroupName)
$KeepBatchCE = @(
    $script:VideoCEName,
    $script:VideoLongCEName,
    $script:OpsCEName
) | Where-Object { $_ -and $_.Trim() -ne "" }
$KeepBatchQueue = @(
    $script:VideoQueueName,
    $script:VideoLongQueueName,
    $script:OpsQueueName
) | Where-Object { $_ -and $_.Trim() -ne "" }
$KeepSGNames = @("academy-v1-sg-app", "academy-v1-sg-batch", "academy-v1-sg-data", "default")
$KeepSGIds = @(
    $script:SecurityGroupApp,
    $script:BatchSecurityGroupId,
    $script:SecurityGroupData
) | Where-Object { $_ -and $_.Trim() -ne "" }
$BuildTagKey = $script:BuildTagKey
$BuildTagValue = $script:BuildTagValue
$BatchOpsASGPrefix = "academy-v1-video-ops-ce-asg-"
$BatchStandardASGPrefix = "academy-v1-video-batch-ce"  # Batch managed ASG name may contain this

Write-Host "`n=== V1 AWS 리소스 인벤토리 (리전 $R) ===" -ForegroundColor Cyan

# --- EC2 ---
$ec2List = @()
if ($VpcId) {
    $res = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $R, "--output", "json")
    if ($res -and $res.Reservations) {
        foreach ($rev in $res.Reservations) {
            foreach ($i in $rev.Instances) {
                $name = ($i.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
                $ec2List += [PSCustomObject]@{ InstanceId = $i.InstanceId; State = $i.State.Name; Name = $name; VpcId = $i.VpcId }
            }
        }
    }
}
# --- ASG ---
$asgList = @()
$asgRes = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
if ($asgRes -and $asgRes.AutoScalingGroups) {
    foreach ($a in $asgRes.AutoScalingGroups) {
        $match = $a.AutoScalingGroupName -in $KeepASG -or $a.AutoScalingGroupName -like "${BatchOpsASGPrefix}*"
        $asgList += [PSCustomObject]@{ Name = $a.AutoScalingGroupName; Desired = $a.DesiredCapacity; Min = $a.MinSize; Max = $a.MaxSize; SSOT = $(if ($match) { "KEEP" } else { "LEGACY_CANDIDATE" }) }
    }
}
# --- EIP ---
$eipList = @()
$natAllocId = $null
if ($VpcId) {
    $natRes = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$VpcId", "Name=state,Values=available", "--region", $R, "--output", "json")
    if ($natRes -and $natRes.NatGateways -and $natRes.NatGateways.Count -gt 0 -and $natRes.NatGateways[0].NatGatewayAddresses.Count -gt 0) {
        $natAllocId = $natRes.NatGateways[0].NatGatewayAddresses[0].AllocationId
    }
}
$eipRes = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
if ($eipRes -and $eipRes.Addresses) {
    foreach ($a in $eipRes.Addresses) {
        $isNat = ($natAllocId -and $a.AllocationId -eq $natAllocId)
        $attached = [bool]$a.AssociationId
        $ssot = if ($isNat -or $attached) { "KEEP" } else { "LEGACY_CANDIDATE" }
        $eipList += [PSCustomObject]@{ AllocationId = $a.AllocationId; PublicIp = $a.PublicIp; AssociationId = $a.AssociationId; SSOT = $ssot }
    }
}
# --- Security Groups (VPC) ---
$sgList = @()
if ($VpcId) {
    $sgRes = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $R, "--output", "json")
    if ($sgRes -and $sgRes.SecurityGroups) {
        foreach ($sg in $sgRes.SecurityGroups) {
            $keep = $sg.GroupName -in $KeepSGNames -or $sg.GroupId -in $KeepSGIds
            $eniRes = Invoke-AwsJson @("ec2", "describe-network-interfaces", "--filters", "Name=group-id,Values=$($sg.GroupId)", "--region", $R, "--output", "json")
            $eniCount = if ($eniRes -and $eniRes.NetworkInterfaces) { $eniRes.NetworkInterfaces.Count } else { 0 }
            $ssot = if ($keep) { "KEEP" } else { "LEGACY_CANDIDATE" }
            $sgList += [PSCustomObject]@{ GroupId = $sg.GroupId; GroupName = $sg.GroupName; ENICount = $eniCount; SSOT = $ssot }
        }
    }
}
# --- Batch CE ---
$ceList = @()
$ceRes = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
if ($ceRes -and $ceRes.computeEnvironments) {
    foreach ($ce in $ceRes.computeEnvironments) {
        $match = $ce.computeEnvironmentName -in $KeepBatchCE
        $ceList += [PSCustomObject]@{ Name = $ce.computeEnvironmentName; State = $ce.state; Status = $ce.status; SSOT = $(if ($match) { "KEEP" } else { "LEGACY_CANDIDATE" }) }
    }
}
# --- Batch Job Queues ---
$queueList = @()
$qRes = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
if ($qRes -and $qRes.jobQueues) {
    foreach ($q in $qRes.jobQueues) {
        $match = $q.jobQueueName -in $KeepBatchQueue
        $queueList += [PSCustomObject]@{ Name = $q.jobQueueName; State = $q.state; SSOT = $(if ($match) { "KEEP" } else { "LEGACY_CANDIDATE" }) }
    }
}
# --- ALB ---
$albList = @()
$albRes = Invoke-AwsJson @("elbv2", "describe-load-balancers", "--region", $R, "--output", "json")
if ($albRes -and $albRes.LoadBalancers) {
    foreach ($lb in $albRes.LoadBalancers) {
        $match = $lb.LoadBalancerName -in $KeepALB
        $albList += [PSCustomObject]@{ Name = $lb.LoadBalancerName; Scheme = $lb.Scheme; VpcId = $lb.VpcId; SSOT = $(if ($match) { "KEEP" } else { "LEGACY_CANDIDATE" }) }
    }
}
# --- Target Groups ---
$tgList = @()
$tgRes = Invoke-AwsJson @("elbv2", "describe-target-groups", "--region", $R, "--output", "json")
if ($tgRes -and $tgRes.TargetGroups) {
    foreach ($tg in $tgRes.TargetGroups) {
        $match = $tg.TargetGroupName -in $KeepTG
        $tgList += [PSCustomObject]@{ Name = $tg.TargetGroupName; Port = $tg.Port; VpcId = $tg.VpcId; SSOT = $(if ($match) { "KEEP" } else { "LEGACY_CANDIDATE" }) }
    }
}

# --- Used instance IDs (keep ASG + build tag + Batch-managed) ---
$usedInstanceIds = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($asg in $asgRes.AutoScalingGroups) {
    $keep = $asg.AutoScalingGroupName -in $KeepASG -or $asg.AutoScalingGroupName -like "${BatchOpsASGPrefix}*" -or $asg.AutoScalingGroupName -like "*academy-v1-video*"
    if (-not $keep) { continue }
    foreach ($inst in $asg.Instances) { [void]$usedInstanceIds.Add($inst.InstanceId) }
}
# Batch CE ECS cluster 인스턴스도 유지
if ($ceRes -and $ceRes.computeEnvironments) {
    foreach ($ce in $ceRes.computeEnvironments) {
        if ($ce.computeEnvironmentName -notin $KeepBatchCE) { continue }
        if ($ce.ecsClusterArn) {
            $clusterName = $ce.ecsClusterArn -replace '^.*/', ''
            $ciRes = Invoke-AwsJson @("ecs", "list-container-instances", "--cluster", $clusterName, "--region", $R, "--output", "json")
            if ($ciRes -and $ciRes.containerInstanceArns -and $ciRes.containerInstanceArns.Count -gt 0) {
                $desc = Invoke-AwsJson @("ecs", "describe-container-instances", "--cluster", $clusterName, "--container-instances", ($ciRes.containerInstanceArns -join ","), "--region", $R, "--output", "json")
                if ($desc -and $desc.containerInstances) {
                    foreach ($ci in $desc.containerInstances) { if ($ci.ec2InstanceId) { [void]$usedInstanceIds.Add($ci.ec2InstanceId) } }
                }
            }
        }
    }
}
$buildRes = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=tag:$BuildTagKey,Values=$BuildTagValue", "Name=instance-state-name,Values=running,pending,stopped", "--region", $R, "--output", "json")
if ($buildRes -and $buildRes.Reservations) {
    foreach ($rev in $buildRes.Reservations) { foreach ($i in $rev.Instances) { [void]$usedInstanceIds.Add($i.InstanceId) } }
}

# 정리 대상 식별
$orphanEIPs = @($eipList | Where-Object { $_.SSOT -eq "LEGACY_CANDIDATE" -and -not $_.AssociationId })
$unusedSGs = @($sgList | Where-Object { $_.SSOT -eq "LEGACY_CANDIDATE" -and $_.ENICount -eq 0 })
$buildInstances = @()
foreach ($rev in (Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=tag:$BuildTagKey,Values=$BuildTagValue", "--region", $R, "--output", "json")).Reservations) {
    foreach ($i in $rev.Instances) { $buildInstances += $i }
}
$orphanEC2 = [System.Collections.ArrayList]::new()
foreach ($rev in (Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=vpc-id,Values=$VpcId", "Name=instance-state-name,Values=running,pending,stopped", "--region", $R, "--output", "json")).Reservations) {
    foreach ($i in $rev.Instances) {
        if ($usedInstanceIds.Contains($i.InstanceId)) { continue }
        $name = ($i.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
        [void]$orphanEC2.Add([PSCustomObject]@{ InstanceId = $i.InstanceId; Name = $name; State = $i.State.Name })
    }
}

# --- aws-resource-inventory.latest.md ---
$invPath = Join-Path $RepoRoot "docs\00-SSOT\v1\reports\aws-resource-inventory.latest.md"
$invDir = Split-Path $invPath -Parent
if (-not (Test-Path $invDir)) { New-Item -ItemType Directory -Path $invDir -Force | Out-Null }
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# AWS 리소스 인벤토리 (V1 SSOT 기준)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**리전:** $R **VPC:** $VpcId **생성:** $(Get-Date -Format 'o')")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## EC2 인스턴스")
[void]$sb.AppendLine("| InstanceId | State | Name | SSOT |")
[void]$sb.AppendLine("|------------|-------|------|------|")
foreach ($e in $ec2List) {
    $ssot = if ($usedInstanceIds.Contains($e.InstanceId)) { "KEEP" } elseif ($e.Name -eq $BuildTagValue) { "KEEP (build, stop 권장)" } else { "LEGACY_CANDIDATE" }
    [void]$sb.AppendLine("| $($e.InstanceId) | $($e.State) | $($e.Name) | $ssot |")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Auto Scaling Groups")
[void]$sb.AppendLine("| Name | Desired | Min | Max | SSOT |")
[void]$sb.AppendLine("|------|---------|-----|-----|------|")
foreach ($a in $asgList) { [void]$sb.AppendLine("| $($a.Name) | $($a.Desired) | $($a.Min) | $($a.Max) | $($a.SSOT) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Elastic IPs")
[void]$sb.AppendLine("| AllocationId | PublicIp | AssociationId | SSOT |")
[void]$sb.AppendLine("|--------------|----------|---------------|------|")
foreach ($e in $eipList) { [void]$sb.AppendLine("| $($e.AllocationId) | $($e.PublicIp) | $($e.AssociationId) | $($e.SSOT) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Security Groups (VPC)")
[void]$sb.AppendLine("| GroupId | GroupName | ENI 수 | SSOT |")
[void]$sb.AppendLine("|---------|-----------|--------|------|")
foreach ($s in $sgList) { [void]$sb.AppendLine("| $($s.GroupId) | $($s.GroupName) | $($s.ENICount) | $($s.SSOT) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Batch Compute Environments")
[void]$sb.AppendLine("| Name | State | Status | SSOT |")
[void]$sb.AppendLine("|------|-------|--------|------|")
foreach ($c in $ceList) { [void]$sb.AppendLine("| $($c.Name) | $($c.State) | $($c.Status) | $($c.SSOT) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Batch Job Queues")
[void]$sb.AppendLine("| Name | State | SSOT |")
[void]$sb.AppendLine("|------|-------|------|")
foreach ($q in $queueList) { [void]$sb.AppendLine("| $($q.Name) | $($q.State) | $($q.SSOT) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Load Balancers")
[void]$sb.AppendLine("| Name | Scheme | VpcId | SSOT |")
[void]$sb.AppendLine("|------|--------|-------|------|")
foreach ($a in $albList) { [void]$sb.AppendLine("| $($a.Name) | $($a.Scheme) | $($a.VpcId) | $($a.SSOT) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Target Groups")
[void]$sb.AppendLine("| Name | Port | VpcId | SSOT |")
[void]$sb.AppendLine("|------|------|-------|------|")
foreach ($t in $tgList) { [void]$sb.AppendLine("| $($t.Name) | $($t.Port) | $($t.VpcId) | $($t.SSOT) |") }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("SSOT 유지: API ASG/ALB/TG, Workers ASG, Batch CE/Queue, academy-db, academy-v1-redis. 그 외 LEGACY_CANDIDATE.")
Set-Content -Path $invPath -Value $sb.ToString() -Encoding UTF8 -Force
Write-Host "  인벤토리: $invPath" -ForegroundColor Green

# --- resource-cleanup-plan.latest.md ---
$planPath = Join-Path $RepoRoot "docs\00-SSOT\v1\reports\resource-cleanup-plan.latest.md"
$planSb = [System.Text.StringBuilder]::new()
[void]$planSb.AppendLine("# V1 리소스 정리 계획 (비용 절감)")
[void]$planSb.AppendLine("")
[void]$planSb.AppendLine("**리전:** $R **생성:** $(Get-Date -Format 'o') **전제:** 서비스 런칭 전, SSOT 명시 리소스 삭제 금지.")
[void]$planSb.AppendLine("")
[void]$planSb.AppendLine("## 삭제/정리 대상")
[void]$planSb.AppendLine("")
[void]$planSb.AppendLine("| 대상 | 삭제/동작 | 삭제 이유 | SSOT 매칭 | 예상 비용 절감 |")
[void]$planSb.AppendLine("|------|------------|-----------|------------|-----------------|")
# EIP
foreach ($e in $orphanEIPs) {
    [void]$planSb.AppendLine("| EIP $($e.AllocationId) ($($e.PublicIp)) | release | EC2 미연결 | LEGACY_CANDIDATE | ~\$3.65/월 |")
}
# Build server
foreach ($b in $buildInstances) {
    $name = ($b.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
    [void]$planSb.AppendLine("| EC2 $($b.InstanceId) ($name) | stop | Build 서버 사용 시에만 기동 | KEEP(stop) | Stopped 시 인스턴스 요금만 절감(스토리지 유지) |")
}
# Orphan EC2 (excluding build - build is stop only)
$orphanForTerminate = @($orphanEC2 | Where-Object { $_.Name -ne $BuildTagValue })
foreach ($o in $orphanForTerminate) {
    [void]$planSb.AppendLine("| EC2 $($o.InstanceId) ($($o.Name)) | terminate | 어떤 유지 ASG에도 속하지 않음 | LEGACY_CANDIDATE | 인스턴스+스토리지 절감 |")
}
# Unused SG
foreach ($s in $unusedSGs) {
    [void]$planSb.AppendLine("| SG $($s.GroupId) ($($s.GroupName)) | delete | ENI 연결 없음 | LEGACY_CANDIDATE | 직접 비용 없음(정리) |")
}
[void]$planSb.AppendLine("")
[void]$planSb.AppendLine("## 실행 방법")
[void]$planSb.AppendLine("```powershell")
[void]$planSb.AppendLine("pwsh -NoProfile -File scripts/v1/run-with-env.ps1 -- pwsh -NoProfile -File scripts/v1/cleanup-legacy.ps1   # DryRun 기본")
[void]$planSb.AppendLine("pwsh -NoProfile -File scripts/v1/run-with-env.ps1 -- pwsh -NoProfile -File scripts/v1/cleanup-legacy.ps1 -Execute   # 실제 적용")
[void]$planSb.AppendLine("```")
[void]$planSb.AppendLine("")
[void]$planSb.AppendLine('Before cleanup, confirm this plan and aws-resource-inventory.latest.md.')
Set-Content -Path $planPath -Value $planSb.ToString() -Encoding UTF8 -Force
Write-Host "  정리 계획: $planPath" -ForegroundColor Green
Write-Host "`n=== 완료 ===`n" -ForegroundColor Cyan
