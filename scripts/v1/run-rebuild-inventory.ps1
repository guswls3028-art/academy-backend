# V1 Stateless Compute 재구축 — 인벤토리 스냅샷 (read-only)
# 결과: docs/00-SSOT/v1/reports/rebuild-inventory.latest.md 갱신
param([string]$AwsProfile = "")
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$ReportsDir = Join-Path $RepoRoot "docs\00-SSOT\v1\reports"
$OutPath = Join-Path $ReportsDir "rebuild-inventory.latest.md"

. (Join-Path $ScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
    Write-Host "Using AWS_PROFILE: $env:AWS_PROFILE" -ForegroundColor Gray
}

. (Join-Path $ScriptRoot "core\ssot.ps1")
$null = $null
$null = Load-SSOT -Env prod
$script:PlanMode = $true  # read-only 보장
$R = $script:Region
if (-not $R -or $R.Trim() -eq "") {
    $R = if ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } else { "ap-northeast-2" }
}
$R = $R.Trim()
if (-not $R) {
    try {
        $raw2 = Get-Content (Join-Path $RepoRoot "docs/00-SSOT/v1/params.yaml") -Raw
        if ($raw2 -match '(?m)^\s*region:\s*([a-z0-9-]+)\s*$') { $R = $matches[1].Trim() }
    } catch { }
}
if (-not $R) { $R = "ap-northeast-2" }
$VpcId = $script:VpcId
if (-not $VpcId -or $VpcId.Trim() -eq "") {
    try {
        $raw = Get-Content (Join-Path $RepoRoot "docs/00-SSOT/v1/params.yaml") -Raw
        if ($raw -match 'vpcId:\s*\"(vpc-[a-zA-Z0-9]+)\"') { $VpcId = $matches[1] }
    } catch { }
}

function AwsJson {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try {
        $str = ($out | Out-String).Trim()
        if (-not $str) { return $null }
        return $str | ConvertFrom-Json
    } catch { return $null }
}

$generated = Get-Date -Format "o"

# EC2 running (tag 의존 제거: ASG 소속 + Name prefix로 필터)
$inst = AwsJson @("ec2","describe-instances","--filters","Name=instance-state-name,Values=running","--region",$R,"--output","json")
$instances = @()
if ($inst -and $inst.Reservations) {
    foreach ($rev in $inst.Reservations) {
        foreach ($i in $rev.Instances) {
            $name = ($i.Tags | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
            $instances += [PSCustomObject]@{
                InstanceId = $i.InstanceId
                Name       = $name
                SubnetId   = $i.SubnetId
                PublicIp   = $i.PublicIpAddress
                PrivateIp  = $i.PrivateIpAddress
            }
        }
    }
}

# ASG (v1 관련)
$asg = AwsJson @("autoscaling","describe-auto-scaling-groups","--region",$R,"--output","json")
$asgs = @()
$asgInstanceIds = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
if ($asg -and $asg.AutoScalingGroups) {
    foreach ($a in $asg.AutoScalingGroups) {
        if ($a.AutoScalingGroupName -like "academy-v1-*") {
            $asgs += [PSCustomObject]@{
                Name    = $a.AutoScalingGroupName
                Min     = $a.MinSize
                Desired = $a.DesiredCapacity
                Max     = $a.MaxSize
                Subnets = $a.VPCZoneIdentifier
            }
            foreach ($inst2 in @($a.Instances)) {
                if ($inst2 -and $inst2.InstanceId) { [void]$asgInstanceIds.Add([string]$inst2.InstanceId) }
            }
        }
    }
}

# ALB/TG
$alb = $null
if ($script:ApiAlbName) {
    $alb = AwsJson @("elbv2","describe-load-balancers","--names",$script:ApiAlbName,"--region",$R,"--output","json")
}
$tg = $null
$healthy = 0
$total = 0
$healthPath = ""
if ($script:ApiTargetGroupName) {
    $tg = AwsJson @("elbv2","describe-target-groups","--names",$script:ApiTargetGroupName,"--region",$R,"--output","json")
    if ($tg -and $tg.TargetGroups -and $tg.TargetGroups.Count -gt 0) {
        $healthPath = $tg.TargetGroups[0].HealthCheckPath
        $tgArn = $tg.TargetGroups[0].TargetGroupArn
        $th = AwsJson @("elbv2","describe-target-health","--target-group-arn",$tgArn,"--region",$R,"--output","json")
        if ($th -and $th.TargetHealthDescriptions) {
            $total = $th.TargetHealthDescriptions.Count
            $healthy = @($th.TargetHealthDescriptions | Where-Object { $_.TargetHealth.State -eq "healthy" }).Count
        }
    }
}

# Batch CE/Queue/JobDef (names from SSOT arrays)
$batchLines = [System.Collections.ArrayList]::new()
foreach ($ceName in @($script:SSOT_CE | Where-Object { $_ })) {
    $ce = AwsJson @("batch","describe-compute-environments","--compute-environments",$ceName,"--region",$R,"--output","json")
    $status = "not found"
    $state = ""
    if ($ce -and $ce.computeEnvironments -and $ce.computeEnvironments.Count -gt 0) {
        $status = $ce.computeEnvironments[0].status
        $state = $ce.computeEnvironments[0].state
    }
    [void]$batchLines.Add([PSCustomObject]@{ Type="CE"; Name=$ceName; StatusState=("$status/$state"); Notes="" })
}
foreach ($qName in @($script:SSOT_Queue | Where-Object { $_ })) {
    $q = AwsJson @("batch","describe-job-queues","--job-queues",$qName,"--region",$R,"--output","json")
    $state = "not found"
    if ($q -and $q.jobQueues -and $q.jobQueues.Count -gt 0) { $state = $q.jobQueues[0].state }
    [void]$batchLines.Add([PSCustomObject]@{ Type="Queue"; Name=$qName; StatusState=$state; Notes="" })
}
foreach ($jdName in @($script:SSOT_JobDef | Where-Object { $_ })) {
    $jd = AwsJson @("batch","describe-job-definitions","--job-definition-name",$jdName,"--status","ACTIVE","--region",$R,"--output","json")
    $rev = ""
    if ($jd -and $jd.jobDefinitions -and $jd.jobDefinitions.Count -gt 0) {
        $rev = ($jd.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1).revision
    } else { $rev = "not found" }
    [void]$batchLines.Add([PSCustomObject]@{ Type="JobDef"; Name=$jdName; StatusState=$rev; Notes="" })
}

# EventBridge
$ebLines = [System.Collections.ArrayList]::new()
foreach ($ruleName in @($script:SSOT_EventBridgeRule | Where-Object { $_ })) {
    $r = AwsJson @("events","describe-rule","--name",$ruleName,"--region",$R,"--output","json")
    $state = if ($r) { $r.State } else { "not found" }
    $targets = AwsJson @("events","list-targets-by-rule","--rule",$ruleName,"--region",$R,"--output","json")
    $tCount = if ($targets -and $targets.Targets) { $targets.Targets.Count } else { 0 }
    [void]$ebLines.Add([PSCustomObject]@{ Rule=$ruleName; State=$state; Targets=$tCount })
}

# NAT/EIP
$nat = AwsJson @("ec2","describe-nat-gateways","--filter","Name=vpc-id,Values=$VpcId","--region",$R,"--output","json")
$natCount = if ($nat -and $nat.NatGateways) { @($nat.NatGateways | Where-Object { $_.State -ne "deleted" }).Count } else { 0 }
$addrs = AwsJson @("ec2","describe-addresses","--region",$R,"--output","json")
$eipTotal = if ($addrs -and $addrs.Addresses) { $addrs.Addresses.Count } else { 0 }
$eipServiceManaged = if ($addrs -and $addrs.Addresses) { @($addrs.Addresses | Where-Object { $_.ServiceManaged }).Count } else { 0 }
$eipUserManaged = if ($addrs -and $addrs.Addresses) { @($addrs.Addresses | Where-Object { -not $_.ServiceManaged }).Count } else { 0 }
$eipUserOrphan = if ($addrs -and $addrs.Addresses) { @($addrs.Addresses | Where-Object { (-not $_.ServiceManaged) -and (-not $_.AssociationId) }).Count } else { 0 }

# SG (VPC) + ENI count
$sgLines = [System.Collections.ArrayList]::new()
if ($VpcId) {
    $sg = AwsJson @("ec2","describe-security-groups","--filters","Name=vpc-id,Values=$VpcId","--region",$R,"--output","json")
    if ($sg -and $sg.SecurityGroups) {
        foreach ($g in $sg.SecurityGroups) {
            $eni = AwsJson @("ec2","describe-network-interfaces","--filters","Name=group-id,Values=$($g.GroupId)","--region",$R,"--output","json")
            $eniCount = if ($eni -and $eni.NetworkInterfaces) { $eni.NetworkInterfaces.Count } else { 0 }
            [void]$sgLines.Add([PSCustomObject]@{ GroupId=$g.GroupId; GroupName=$g.GroupName; EniCount=$eniCount })
        }
    }
}

$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# V1 Stateless Compute 재구축 — 인벤토리 (스냅샷)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**Generated:** $generated  ")
[void]$sb.AppendLine("**리전:** $R  ")
[void]$sb.AppendLine("**SSOT:** docs/00-SSOT/v1/params.yaml")
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## EC2 running (Project=academy)")
[void]$sb.AppendLine("| InstanceId | Name | SubnetId | PublicIp | PrivateIp |")
[void]$sb.AppendLine("|------------|------|----------|----------|-----------|")
# 표시 기준: (1) Name이 academy-v1-* 이거나 (2) academy-v1 ASG 소속인 인스턴스
$filteredInstances = @($instances | Where-Object {
    (($_.Name -is [string]) -and $_.Name -like "academy-v1-*") -or $asgInstanceIds.Contains([string]$_.InstanceId)
})
if ($filteredInstances.Count -eq 0) { [void]$sb.AppendLine("| (none) | - | - | - | - |") }
else { foreach ($i in $filteredInstances) { [void]$sb.AppendLine("| $($i.InstanceId) | $($i.Name) | $($i.SubnetId) | $($i.PublicIp) | $($i.PrivateIp) |") } }
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## ASG (academy-v1-*)")
[void]$sb.AppendLine("| Name | Min | Desired | Max | Subnets |")
[void]$sb.AppendLine("|------|-----|---------|-----|---------|")
if ($asgs.Count -eq 0) { [void]$sb.AppendLine("| (none) | - | - | - | - |") }
else { foreach ($a in $asgs) { [void]$sb.AppendLine("| $($a.Name) | $($a.Min) | $($a.Desired) | $($a.Max) | $($a.Subnets) |") } }
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## ALB/TG health")
$albName = $script:ApiAlbName
$tgName = $script:ApiTargetGroupName
[void]$sb.AppendLine("| ALB | TG | HealthPath | Healthy/Total |")
[void]$sb.AppendLine("|-----|----|------------|--------------|")
[void]$sb.AppendLine("| $albName | $tgName | $healthPath | $healthy/$total |")
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## Batch (SSOT names)")
[void]$sb.AppendLine("| Type | Name | Status/State | Notes |")
[void]$sb.AppendLine("|------|------|--------------|------|")
foreach ($b in $batchLines) { [void]$sb.AppendLine("| $($b.Type) | $($b.Name) | $($b.StatusState) | $($b.Notes) |") }
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## EventBridge (SSOT rules)")
[void]$sb.AppendLine("| Rule | State | Targets |")
[void]$sb.AppendLine("|------|-------|---------|")
foreach ($r in $ebLines) { [void]$sb.AppendLine("| $($r.Rule) | $($r.State) | $($r.Targets) |") }
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## NAT/EIP/SG")
[void]$sb.AppendLine("| Item | Value | Notes |")
[void]$sb.AppendLine("|------|-------|------|")
[void]$sb.AppendLine("| NAT gateways (non-deleted) | $natCount | network.natEnabled=false 목표 |")
[void]$sb.AppendLine("| EIP total (all) | $eipTotal | 참고 |")
[void]$sb.AppendLine("| EIP service-managed (alb/rds 등) | $eipServiceManaged | AWS 서비스 관리 (보통 직접 release 불가) |")
[void]$sb.AppendLine("| EIP user-managed | $eipUserManaged | **목표=0** |")
[void]$sb.AppendLine("| EIP user-managed orphan | $eipUserOrphan | orphan이면 즉시 release 후보 |")
[void]$sb.AppendLine("| Security groups (VPC) | $($sgLines.Count) | 목표 ≤ 8 |")
[void]$sb.AppendLine("")

[void]$sb.AppendLine("## Security Groups (VPC)")
[void]$sb.AppendLine("| GroupId | GroupName | ENI count |")
[void]$sb.AppendLine("|---------|-----------|----------|")
$sortedSg = $sgLines | Sort-Object -Property @{ Expression = { $_.EniCount }; Descending = $true }, @{ Expression = { $_.GroupName }; Descending = $false }
foreach ($g in $sortedSg) {
    [void]$sb.AppendLine("| $($g.GroupId) | $($g.GroupName) | $($g.EniCount) |")
}

if (-not (Test-Path $ReportsDir)) { New-Item -ItemType Directory -Path $ReportsDir -Force | Out-Null }
Set-Content -Path $OutPath -Value $sb.ToString() -Encoding UTF8 -Force
Write-Host "Rebuild inventory: $OutPath" -ForegroundColor Green

