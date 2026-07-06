# V1 cost/waste audit. Read-only: collects AWS actuals and cleanup dry-runs,
# then writes docs/reports/cost-waste-audit.latest.md.
param(
    [string]$AwsProfile = "",
    [int]$EcrKeep = 10,
    [int]$BatchJobdefKeep = 5,
    [string]$BudgetName = "academy-monthly-infra",
    [switch]$SkipCleanupDryRuns
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$ReportsDir = Join-Path $RepoRoot "docs\reports"
$ReportPath = Join-Path $ReportsDir "cost-waste-audit.latest.md"

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")

if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
    Write-Host "Using AWS_PROFILE: $env:AWS_PROFILE" -ForegroundColor Gray
}

$null = Load-SSOT -Env "prod"
$R = $script:Region
$VpcId = $script:VpcId

function Get-Prop {
    param($Object, [string]$Name, $Default = $null)
    if ($null -eq $Object) { return $Default }
    $prop = $Object.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $Default }
    if ($null -eq $prop.Value) { return $Default }
    return $prop.Value
}

function Convert-ToInt {
    param($Value, [int]$Default = 0)
    if ($null -eq $Value -or "$Value" -eq "") { return $Default }
    try { return [int]$Value } catch { return $Default }
}

function Convert-ToDouble {
    param($Value, [double]$Default = 0)
    if ($null -eq $Value -or "$Value" -eq "") { return $Default }
    try { return [double]$Value } catch { return $Default }
}

function Format-MdCell {
    param($Value)
    if ($null -eq $Value) { return "" }
    return ("$Value" -replace "\|", "\|" -replace "`r?`n", "<br>")
}

function Add-TableRow {
    param([System.Text.StringBuilder]$Builder, [object[]]$Values)
    $cells = @($Values | ForEach-Object { Format-MdCell $_ })
    [void]$Builder.AppendLine("| $($cells -join ' | ') |")
}

function Get-AsgSummary {
    param($Asg, [int]$SsotMin, [int]$SsotDesired, [int]$SsotMax)
    if ($null -eq $Asg) {
        return [PSCustomObject]@{
            Ssot = "min=$SsotMin desired=$SsotDesired max=$SsotMax"
            Actual = "missing"
            Healthy = 0
            Running = 0
            Disposition = "review"
        }
    }
    $instances = @(Get-Prop $Asg "Instances" @())
    $healthy = @($instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" }).Count
    $actual = "min=$($Asg.MinSize) desired=$($Asg.DesiredCapacity) max=$($Asg.MaxSize), healthy=$healthy"
    $matches = ($Asg.MinSize -eq $SsotMin -and $Asg.MaxSize -eq $SsotMax)
    $disposition = if ($matches) { "confirmed" } else { "capacity drift" }
    return [PSCustomObject]@{
        Ssot = "min=$SsotMin desired=$SsotDesired max=$SsotMax"
        Actual = $actual
        Healthy = $healthy
        Running = $instances.Count
        Disposition = $disposition
    }
}

function Invoke-ProcessText {
    param([string]$FilePath, [string[]]$Arguments)
    $output = & $FilePath @Arguments 2>&1
    $exit = $LASTEXITCODE
    return [PSCustomObject]@{
        ExitCode = $exit
        Text = ($output | Out-String).Trim()
    }
}

Write-Host ""
Write-Host "=== V1 Cost/Waste Audit (read-only) ===" -ForegroundColor Cyan
Write-Host "  Region: $R  VpcId: $VpcId" -ForegroundColor Gray

$asgRes = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--region", $R, "--output", "json")
$allAsgs = @()
if ($asgRes -and $asgRes.AutoScalingGroups) { $allAsgs = @($asgRes.AutoScalingGroups) }
function Find-Asg {
    param([string]$Name)
    return @($allAsgs | Where-Object { $_.AutoScalingGroupName -eq $Name } | Select-Object -First 1)[0]
}

$apiAsg = Find-Asg $script:ApiASGName
$messagingAsg = Find-Asg $script:MessagingASGName
$aiAsg = Find-Asg $script:AiASGName
$toolsAsg = Find-Asg $script:ToolsASGName

$apiSummary = Get-AsgSummary $apiAsg $script:ApiASGMinSize $script:ApiASGDesiredCapacity $script:ApiASGMaxSize
$messagingSummary = Get-AsgSummary $messagingAsg $script:MessagingMinSize $script:MessagingDesiredCapacity $script:MessagingMaxSize
$aiSummary = Get-AsgSummary $aiAsg $script:AiMinSize $script:AiDesiredCapacity $script:AiMaxSize
$toolsSummary = Get-AsgSummary $toolsAsg $script:ToolsMinSize $script:ToolsDesiredCapacity $script:ToolsMaxSize

$ceRes = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
$allCes = @()
if ($ceRes -and $ceRes.computeEnvironments) { $allCes = @($ceRes.computeEnvironments) }
function Find-Ce {
    param([string]$Name)
    return @($allCes | Where-Object { $_.computeEnvironmentName -eq $Name } | Select-Object -First 1)[0]
}

function Get-CeSummary {
    param($Ce, [int]$SsotMin, [int]$SsotMax, [bool]$SsotSpot, [string]$SsotTypes)
    $ssotType = if ($SsotSpot) { "SPOT" } else { "EC2" }
    if ($null -eq $Ce) {
        return [PSCustomObject]@{ Ssot = "min=$SsotMin max=$SsotMax type=$ssotType"; Actual = "missing"; Disposition = "review" }
    }
    $resources = Get-Prop $Ce "computeResources" $null
    $actualType = Get-Prop $resources "type" ""
    $min = Convert-ToInt (Get-Prop $resources "minvCpus" 0)
    $max = Convert-ToInt (Get-Prop $resources "maxvCpus" 0)
    $desired = Convert-ToInt (Get-Prop $resources "desiredvCpus" 0)
    $types = @((Get-Prop $resources "instanceTypes" @()) | Where-Object { $_ }) -join ","
    $actual = "$actualType, state=$($Ce.state)/$($Ce.status), min=$min desired=$desired max=$max, types=$types"
    $ok = ($actualType -eq $ssotType -and $min -eq $SsotMin -and $max -eq $SsotMax)
    return [PSCustomObject]@{
        Ssot = "min=$SsotMin max=$SsotMax type=$ssotType, types=$SsotTypes"
        Actual = $actual
        Disposition = if ($ok) { "confirmed" } else { "capacity/cost drift" }
    }
}

$videoTypes = @($script:VideoCEInstanceTypes | Where-Object { $_ }) -join ","
$videoCeSummary = Get-CeSummary (Find-Ce $script:VideoCEName) $script:VideoCEMinvCpus $script:VideoCEMaxvCpus $script:VideoUseSpot $videoTypes
$opsCeSummary = Get-CeSummary (Find-Ce $script:OpsCEName) 0 $script:OpsCEMaxvCpus $false $script:OpsCEInstanceType

$rdsClass = "unknown"
$rdsPending = "{}"
$rdsStatus = "unknown"
$rdsRes = Invoke-AwsJson @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $R, "--output", "json")
if ($rdsRes -and $rdsRes.DBInstances -and $rdsRes.DBInstances.Count -gt 0) {
    $db = $rdsRes.DBInstances[0]
    $rdsClass = $db.DBInstanceClass
    $rdsStatus = $db.DBInstanceStatus
    $pendingObj = Get-Prop $db "PendingModifiedValues" $null
    if ($pendingObj) {
        $pendingJson = ($pendingObj | ConvertTo-Json -Compress)
        if ($pendingJson -and $pendingJson -ne "null") { $rdsPending = $pendingJson }
    }
}

$redisType = "unknown"
$redisStatus = "unknown"
$redisRes = Invoke-AwsJson @("elasticache", "describe-cache-clusters", "--show-cache-node-info", "--region", $R, "--output", "json")
if ($redisRes -and $redisRes.CacheClusters) {
    $redisCluster = @($redisRes.CacheClusters | Where-Object {
        $_.ReplicationGroupId -eq $script:RedisReplicationGroupId -or $_.CacheClusterId -like "$($script:RedisReplicationGroupId)*"
    } | Select-Object -First 1)[0]
    if ($redisCluster) {
        $redisType = $redisCluster.CacheNodeType
        $redisStatus = $redisCluster.CacheClusterStatus
    }
}

$natAllocations = @()
$natCount = 0
if ($VpcId) {
    $natRes = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$VpcId", "Name=state,Values=available", "--region", $R, "--output", "json")
    if ($natRes -and $natRes.NatGateways) {
        $natCount = @($natRes.NatGateways).Count
        foreach ($nat in @($natRes.NatGateways)) {
            foreach ($addr in @($nat.NatGatewayAddresses)) {
                if ($addr.AllocationId) { $natAllocations += $addr.AllocationId }
            }
        }
    }
}

$addrRes = Invoke-AwsJson @("ec2", "describe-addresses", "--region", $R, "--output", "json")
$allEips = @()
if ($addrRes -and $addrRes.Addresses) { $allEips = @($addrRes.Addresses) }
$unassociatedEips = @($allEips | Where-Object { -not $_.AssociationId -and $_.AllocationId -notin $natAllocations })

$sgCount = 0
$unusedSgs = @()
if ($VpcId) {
    $keepSgNames = @("academy-v1-sg-app", "academy-v1-sg-batch", "academy-v1-sg-data", "academy-rds", "default")
    $keepSgIds = @($script:SecurityGroupApp, $script:BatchSecurityGroupId, $script:SecurityGroupData) | Where-Object { $_ -and $_.Trim() -ne "" }
    $sgRes = Invoke-AwsJson @("ec2", "describe-security-groups", "--filters", "Name=vpc-id,Values=$VpcId", "--region", $R, "--output", "json")
    if ($sgRes -and $sgRes.SecurityGroups) {
        $sgs = @($sgRes.SecurityGroups)
        $sgCount = $sgs.Count
        foreach ($sg in $sgs) {
            if ($sg.GroupName -in $keepSgNames -or $sg.GroupId -in $keepSgIds) { continue }
            $eniRes = Invoke-AwsJson @("ec2", "describe-network-interfaces", "--filters", "Name=group-id,Values=$($sg.GroupId)", "--region", $R, "--output", "json")
            $eniCount = if ($eniRes -and $eniRes.NetworkInterfaces) { @($eniRes.NetworkInterfaces).Count } else { 0 }
            if ($eniCount -eq 0) { $unusedSgs += $sg }
        }
    }
}

$availableVolumes = @()
$volRes = Invoke-AwsJson @("ec2", "describe-volumes", "--filters", "Name=status,Values=available", "--region", $R, "--output", "json")
if ($volRes -and $volRes.Volumes) { $availableVolumes = @($volRes.Volumes) }
$availableVolumeGb = 0
foreach ($v in $availableVolumes) { $availableVolumeGb += Convert-ToInt $v.Size }

$usedInstanceIds = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$keepAsgNames = @($script:ApiASGName, $script:MessagingASGName, $script:AiASGName, $script:ToolsASGName)
$batchAsgPrefixes = @(
    "AWSBatch-$($script:VideoCEName)-asg-",
    "$($script:VideoCEName)-asg-",
    "AWSBatch-$($script:OpsCEName)-asg-",
    "$($script:OpsCEName)-asg-"
) | Where-Object { $_ -and $_.Trim() -ne "" }
foreach ($asg in $allAsgs) {
    $keep = $asg.AutoScalingGroupName -in $keepAsgNames
    if (-not $keep) {
        foreach ($prefix in $batchAsgPrefixes) {
            if ($asg.AutoScalingGroupName -like "$prefix*") { $keep = $true; break }
        }
    }
    if (-not $keep) { continue }
    foreach ($inst in @($asg.Instances)) {
        if ($inst.InstanceId) { [void]$usedInstanceIds.Add($inst.InstanceId) }
    }
}

$runningInstances = @()
$orphanInstances = @()
if ($VpcId) {
    $instRes = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=vpc-id,Values=$VpcId", "Name=instance-state-name,Values=pending,running,stopping,stopped", "--region", $R, "--output", "json")
    if ($instRes -and $instRes.Reservations) {
        foreach ($rev in @($instRes.Reservations)) {
            foreach ($i in @($rev.Instances)) {
                $name = (@($i.Tags) | Where-Object { $_.Key -eq "Name" } | Select-Object -First 1).Value
                $row = [PSCustomObject]@{ Id = $i.InstanceId; Name = $name; State = $i.State.Name; Type = $i.InstanceType }
                if ($i.State.Name -eq "running") { $runningInstances += $row }
                if (-not $usedInstanceIds.Contains($i.InstanceId)) { $orphanInstances += $row }
            }
        }
    }
}

function Get-QueueDepth {
    param([string]$Name, [string]$Url, [string]$DlqSuffix)
    $queueUrl = $Url
    if (-not $queueUrl -and $Name) {
        $urlRes = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $Name, "--region", $R, "--output", "json")
        if ($urlRes -and $urlRes.QueueUrl) { $queueUrl = $urlRes.QueueUrl }
    }
    $visible = 0
    $inFlight = 0
    if ($queueUrl) {
        $attrs = Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $queueUrl, "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible", "--region", $R, "--output", "json")
        if ($attrs -and $attrs.Attributes) {
            $visible = Convert-ToInt (Get-Prop $attrs.Attributes "ApproximateNumberOfMessages" 0)
            $inFlight = Convert-ToInt (Get-Prop $attrs.Attributes "ApproximateNumberOfMessagesNotVisible" 0)
        }
    }
    $dlq = 0
    if ($Name -and $DlqSuffix) {
        $dlqName = "$Name$DlqSuffix"
        $dlqUrlRes = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $dlqName, "--region", $R, "--output", "json")
        if ($dlqUrlRes -and $dlqUrlRes.QueueUrl) {
            $dlqAttrs = Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $dlqUrlRes.QueueUrl, "--attribute-names", "ApproximateNumberOfMessages", "--region", $R, "--output", "json")
            if ($dlqAttrs -and $dlqAttrs.Attributes) {
                $dlq = Convert-ToInt (Get-Prop $dlqAttrs.Attributes "ApproximateNumberOfMessages" 0)
            }
        }
    }
    return [PSCustomObject]@{ Name = $Name; Visible = $visible; InFlight = $inFlight; Dlq = $dlq }
}

$queueRows = @(
    (Get-QueueDepth $script:MessagingSqsQueueName $script:MessagingSqsQueueUrl $script:MessagingDlqSuffix),
    (Get-QueueDepth $script:AiSqsQueueName $script:AiSqsQueueUrl $script:AiDlqSuffix),
    (Get-QueueDepth $script:ToolsSqsQueueName $script:ToolsSqsQueueUrl $script:MessagingDlqSuffix)
)

$ecrExit = 0
$ecrImages = 0
$ecrGb = 0.0
$ecrSavings = 0.0
$ecrStatus = "skipped"
$batchExit = 0
$batchKeep = 0
$batchDrop = 0
$batchStatus = "skipped"
if (-not $SkipCleanupDryRuns) {
    Write-Host "  ECR dry-run..." -ForegroundColor Gray
    $ecr = Invoke-ProcessText "python" @((Join-Path $ScriptRoot "ecr-cleanup.py"), "--dry-run", "--keep", "$EcrKeep")
    $ecrExit = $ecr.ExitCode
    $ecrStatus = if ($ecrExit -eq 0) { "ok" } else { "failed(exit=$ecrExit)" }
    if ($ecr.Text -match "Total:\s+(\d+)\s+images,\s+([0-9.]+)\s+GB reclaimable") {
        $ecrImages = Convert-ToInt $matches[1]
        $ecrGb = Convert-ToDouble $matches[2]
    }
    if ($ecr.Text -match 'Est\.\s+monthly savings:\s+\$([0-9.]+)') {
        $ecrSavings = Convert-ToDouble $matches[1]
    }

    Write-Host "  Batch jobdef dry-run..." -ForegroundColor Gray
    $batch = Invoke-ProcessText "python" @((Join-Path $ScriptRoot "batch-jobdef-cleanup.py"), "--dry-run", "--keep", "$BatchJobdefKeep")
    $batchExit = $batch.ExitCode
    $batchStatus = if ($batchExit -eq 0) { "ok" } else { "failed(exit=$batchExit)" }
    if ($batch.Text -match "Totals:\s+keep=(\d+),\s+drop=(\d+)") {
        $batchKeep = Convert-ToInt $matches[1]
        $batchDrop = Convert-ToInt $matches[2]
    }
}

$now = Get-Date
$monthStart = Get-Date -Year $now.Year -Month $now.Month -Day 1
$endExclusive = $now.Date.AddDays(1)
$costRows = @()
$costStatus = "unavailable"
$ceCostRes = Invoke-AwsJson @(
    "ce", "get-cost-and-usage",
    "--time-period", "Start=$($monthStart.ToString('yyyy-MM-dd')),End=$($endExclusive.ToString('yyyy-MM-dd'))",
    "--granularity", "MONTHLY",
    "--metrics", "UnblendedCost",
    "--group-by", "Type=DIMENSION,Key=SERVICE",
    "--region", "us-east-1",
    "--output", "json"
)
if ($ceCostRes -and $ceCostRes.ResultsByTime -and $ceCostRes.ResultsByTime.Count -gt 0) {
    $groups = @($ceCostRes.ResultsByTime[0].Groups)
    foreach ($g in $groups) {
        $service = @($g.Keys)[0]
        $amount = Convert-ToDouble $g.Metrics.UnblendedCost.Amount
        if ($amount -gt 0.005) {
            $costRows += [PSCustomObject]@{ Service = $service; Cost = $amount }
        }
    }
    $costRows = @($costRows | Sort-Object Cost -Descending | Select-Object -First 15)
    $costStatus = "ok"
}

$budgetStatus = "unavailable"
$budgetLine = "Budget '$BudgetName' unavailable"
$budgetRes = Invoke-AwsJson @("budgets", "describe-budget", "--account-id", $script:AccountId, "--budget-name", $BudgetName, "--region", "us-east-1", "--output", "json")
if ($budgetRes -and $budgetRes.Budget) {
    $budget = $budgetRes.Budget
    $limit = Convert-ToDouble $budget.BudgetLimit.Amount
    $actual = Convert-ToDouble $budget.CalculatedSpend.ActualSpend.Amount
    $forecast = Convert-ToDouble $budget.CalculatedSpend.ForecastedSpend.Amount
    $pct = if ($limit -gt 0) { [Math]::Round(($actual / $limit) * 100, 1) } else { 0 }
    $budgetStatus = if ($limit -gt 0 -and $actual -gt $limit) { "over-budget" } elseif ($pct -ge 80) { "watch" } else { "ok" }
    $budgetLine = "actual=$([Math]::Round($actual, 2)) $($budget.BudgetLimit.Unit), limit=$([Math]::Round($limit, 2)), forecast=$([Math]::Round($forecast, 2)), used=$pct%"
}

$actions = [System.Collections.ArrayList]::new()
if ($unassociatedEips.Count -gt 0 -or $unusedSgs.Count -gt 0) {
    [void]$actions.Add("Review ``docs/reports/resource-cleanup.latest.md``, then run ``pwsh -File scripts/v1/run-resource-cleanup.ps1 -AwsProfile default -Execute`` if candidates are valid.")
}
if ($availableVolumes.Count -gt 0) {
    [void]$actions.Add("Review $($availableVolumes.Count) available EBS volume(s), $availableVolumeGb GiB total, before snapshot/delete.")
}
if ($orphanInstances.Count -gt 0) {
    [void]$actions.Add("Review $($orphanInstances.Count) EC2 instance(s) not attached to kept ASGs before terminate/stop decisions.")
}
$dlqTotal = (@($queueRows | Measure-Object -Property Dlq -Sum).Sum)
if ($dlqTotal -gt 0) {
    [void]$actions.Add("Review SQS DLQ message(s) before treating worker queues as fully clean; current DLQ total=$dlqTotal.")
}
if ($ecrImages -gt 0) {
    [void]$actions.Add("Run ``python scripts/v1/ecr-cleanup.py --execute --keep $EcrKeep`` to reclaim about $ecrGb GB (~$ecrSavings/mo).")
}
if ($batchDrop -gt 0) {
    [void]$actions.Add("Run ``python scripts/v1/batch-jobdef-cleanup.py --execute --keep $BatchJobdefKeep`` to deregister $batchDrop old ACTIVE job definition revision(s).")
}
if ($budgetStatus -eq "over-budget" -or $budgetStatus -eq "watch") {
    [void]$actions.Add("Budget status is $budgetStatus; inspect Cost Explorer service rows before changing warm baselines.")
}
if ($actions.Count -eq 0) {
    [void]$actions.Add("No immediate deletion or downsize target found in this audit.")
}

$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# Cost/Waste Audit - Current Runtime")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("**Generated:** $(Get-Date -Format 'o')")
[void]$sb.AppendLine("**Scope:** academy V1 production resources in ``$R``.")
[void]$sb.AppendLine("**Mode:** read-only AWS describe/get/list + cleanup dry-runs.")
[void]$sb.AppendLine('**Truth sources:** AWS actual state, `docs/ssot/params.yaml`, Cost Explorer, AWS Budget, ECR/Batch cleanup dry-runs, and resource cleanup checks.')
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Confirmed Facts")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Check | Result | Disposition |")
[void]$sb.AppendLine("|-------|--------|-------------|")
Add-TableRow $sb @("AWS Budget", $budgetLine, $budgetStatus)
Add-TableRow $sb @("Cost Explorer", "$costStatus; period $($monthStart.ToString('yyyy-MM-dd')) through $($endExclusive.AddDays(-1).ToString('yyyy-MM-dd'))", "monthly-to-date")
Add-TableRow $sb @("ECR cleanup dry-run", "$ecrImages image(s), $ecrGb GB reclaimable, status=$ecrStatus", $(if ($ecrImages -gt 0) { "cleanup candidate" } else { "no ECR deletion needed" }))
Add-TableRow $sb @("Batch jobdef cleanup dry-run", "keep=$batchKeep, drop=$batchDrop, status=$batchStatus", $(if ($batchDrop -gt 0) { "cleanup candidate" } else { "no deregistration needed" }))
Add-TableRow $sb @("RDS class", "$rdsClass, status=$rdsStatus, pending=$rdsPending", $(if ($rdsClass -eq $script:RdsInstanceClass) { "matches SSOT" } else { "class drift" }))
Add-TableRow $sb @("Redis node", "$redisType, status=$redisStatus", $(if ($redisType -eq $script:RedisNodeType) { "matches SSOT" } else { "node type drift" }))
Add-TableRow $sb @("Running EC2 in academy VPC", "$($runningInstances.Count)", "API/Messaging warm baseline plus active worker/batch bursts")
Add-TableRow $sb @("NAT Gateway", "$natCount available", $(if ($natCount -eq 0) { "matches NAT-off posture" } else { "review recurring VPC cost" }))
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Capacity SSOT vs Actual")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Component | SSOT | Actual | Disposition |")
[void]$sb.AppendLine("|-----------|------|--------|-------------|")
Add-TableRow $sb @("API ASG", $apiSummary.Ssot, $apiSummary.Actual, $apiSummary.Disposition)
Add-TableRow $sb @("Messaging worker ASG", $messagingSummary.Ssot, $messagingSummary.Actual, $messagingSummary.Disposition)
Add-TableRow $sb @("AI worker ASG", $aiSummary.Ssot, $aiSummary.Actual, $aiSummary.Disposition)
Add-TableRow $sb @("Tools worker ASG", $toolsSummary.Ssot, $toolsSummary.Actual, $toolsSummary.Disposition)
Add-TableRow $sb @("Video Batch CE", $videoCeSummary.Ssot, $videoCeSummary.Actual, $videoCeSummary.Disposition)
Add-TableRow $sb @("Video Ops CE", $opsCeSummary.Ssot, $opsCeSummary.Actual, $opsCeSummary.Disposition)
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Waste Checks")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Check | Result | Disposition |")
[void]$sb.AppendLine("|-------|--------|-------------|")
Add-TableRow $sb @("Unassociated Elastic IP", "$($unassociatedEips.Count)", $(if ($unassociatedEips.Count -eq 0) { "clean" } else { "release candidate" }))
Add-TableRow $sb @("Unused Security Group", "$($unusedSgs.Count) / total SG $sgCount", $(if ($unusedSgs.Count -eq 0) { "clean" } else { "delete candidate" }))
Add-TableRow $sb @("Available EBS volume", "$($availableVolumes.Count), $availableVolumeGb GiB", $(if ($availableVolumes.Count -eq 0) { "clean" } else { "review snapshot/delete" }))
Add-TableRow $sb @("Orphan EC2 in academy VPC", "$($orphanInstances.Count)", $(if ($orphanInstances.Count -eq 0) { "clean" } else { "review terminate/stop" }))
Add-TableRow $sb @("Batch compute", "standard=$($videoCeSummary.Actual); ops=$($opsCeSummary.Actual)", "idle desired should remain 0 outside jobs")
foreach ($q in $queueRows) {
    Add-TableRow $sb @("SQS $($q.Name)", "visible=$($q.Visible), in-flight=$($q.InFlight), DLQ=$($q.Dlq)", $(if ($q.Visible -eq 0 -and $q.InFlight -eq 0 -and $q.Dlq -eq 0) { "clean" } else { "workload/backlog present" }))
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Cost Explorer Snapshot")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("Time period: $($monthStart.ToString('yyyy-MM-dd')) through $($endExclusive.AddDays(-1).ToString('yyyy-MM-dd')), unblended cost, estimated.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Service | Cost |")
[void]$sb.AppendLine("|---------|------|")
if ($costRows.Count -eq 0) {
    Add-TableRow $sb @("unavailable", "0.00 USD")
} else {
    foreach ($row in $costRows) {
        Add-TableRow $sb @($row.Service, "$('{0:N2}' -f $row.Cost) USD")
    }
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Recommended Actions")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Action |")
[void]$sb.AppendLine("|--------|")
foreach ($action in $actions) { Add-TableRow $sb @($action) }
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Policy Decisions Retained")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Item | Status |")
[void]$sb.AppendLine("|------|--------|")
Add-TableRow $sb @("API warm baseline", 'kept at one `t4g.medium`; target tracking keeps headroom for public API latency.')
Add-TableRow $sb @("Messaging worker warm baseline", 'kept at one `t4g.medium`; account recovery and Alimtalk wait paths should not cold-start.')
Add-TableRow $sb @("AI/Tools workers", "scale-to-zero policy retained; queue alarms/API wake-up own burst scale-out.")
Add-TableRow $sb @("Standard video encoding", "Spot Batch CE retained; paid encode tests are not submitted by this audit.")
Add-TableRow $sb @("RDS/Redis", "current small baseline retained until metric evidence supports a safer right-size move.")

if (-not (Test-Path $ReportsDir)) { New-Item -ItemType Directory -Path $ReportsDir -Force | Out-Null }
[System.IO.File]::WriteAllText(
    $ReportPath,
    $sb.ToString().TrimEnd() + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "  cost-waste-audit.latest.md: $ReportPath" -ForegroundColor Green
Write-Host "=== Done ===" -ForegroundColor Cyan
