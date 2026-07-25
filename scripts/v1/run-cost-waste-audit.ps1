# V1 cost/waste audit. Read-only: collects AWS actuals and cleanup dry-runs,
# then writes docs/reports/cost-waste-audit.latest.md.
param(
    [string]$AwsProfile = "",
    [int]$EcrKeep = 10,
    [int]$BatchJobdefKeep = 5,
    [string]$BudgetName = "academy-monthly-infra",
    [ValidateRange(30, 365)]
    [int]$UsageDays = 90,
    [ValidateRange(7, 90)]
    [int]$RecentDays = 30,
    [switch]$SkipHostMemory,
    [switch]$SkipCleanupDryRuns
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$ReportsDir = Join-Path $RepoRoot "docs\reports"
$ReportPath = Join-Path $ReportsDir "cost-waste-audit.latest.md"
$HistoryDir = Join-Path $ReportsDir "history"

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

function Get-CloudWatchMetricSummary {
    param(
        [string]$Namespace,
        [string]$MetricName,
        [string]$DimensionName,
        [string]$DimensionValue
    )

    $endTime = (Get-Date).ToUniversalTime()
    $startTime = $endTime.AddDays(-$UsageDays)
    # Six-hour buckets stay below CloudWatch's 1,440-point limit and preserve
    # each bucket's Maximum/Minimum for burst and headroom decisions.
    $periodSeconds = 21600
    $res = Invoke-AwsJson @(
        "cloudwatch", "get-metric-statistics",
        "--namespace", $Namespace,
        "--metric-name", $MetricName,
        "--dimensions", "Name=$DimensionName,Value=$DimensionValue",
        "--start-time", $startTime.ToString("o"),
        "--end-time", $endTime.ToString("o"),
        "--period", "$periodSeconds",
        "--statistics", "Average", "Maximum", "Minimum", "Sum",
        "--region", $R,
        "--output", "json"
    )
    $points = if ($res -and $res.Datapoints) { @($res.Datapoints) } else { @() }

    function Measure-Window {
        param([int]$Days)
        $cutoff = $endTime.AddDays(-$Days)
        $window = @($points | Where-Object { [datetime]$_.Timestamp -ge $cutoff })
        if ($window.Count -eq 0) {
            return [PSCustomObject]@{
                Days = $Days
                Samples = 0
                Average = $null
                Peak = $null
                Low = $null
                Total = $null
            }
        }
        return [PSCustomObject]@{
            Days = $Days
            Samples = $window.Count
            Average = Convert-ToDouble (($window | Measure-Object -Property Average -Average).Average)
            Peak = Convert-ToDouble (($window | Measure-Object -Property Maximum -Maximum).Maximum)
            Low = Convert-ToDouble (($window | Measure-Object -Property Minimum -Minimum).Minimum)
            Total = Convert-ToDouble (($window | Measure-Object -Property Sum -Sum).Sum)
        }
    }

    return [PSCustomObject]@{
        Namespace = $Namespace
        Metric = $MetricName
        Dimension = $DimensionValue
        Recent = Measure-Window $RecentDays
        Long = Measure-Window $UsageDays
    }
}

function Get-LatestLaunchTemplateType {
    param([string]$LaunchTemplateName)
    if (-not $LaunchTemplateName) { return "unknown" }
    $res = Invoke-AwsJson @(
        "ec2", "describe-launch-template-versions",
        "--launch-template-name", $LaunchTemplateName,
        "--versions", '$Latest',
        "--region", $R,
        "--output", "json"
    )
    if ($res -and $res.LaunchTemplateVersions -and $res.LaunchTemplateVersions.Count -gt 0) {
        return "$($res.LaunchTemplateVersions[0].LaunchTemplateData.InstanceType)"
    }
    return "unknown"
}

function Get-AsgLiveMemory {
    param($Asg, [string]$Label)
    if ($SkipHostMemory) {
        return [PSCustomObject]@{ Status = "skipped"; TotalBytes = 0; UsedBytes = 0; AvailableBytes = 0; AvailablePercent = 0; Container = "" }
    }
    $instance = @((Get-Prop $Asg "Instances" @()) | Where-Object {
        $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy"
    } | Select-Object -First 1)
    if ($instance.Count -eq 0) {
        return [PSCustomObject]@{ Status = "no-running-instance"; TotalBytes = 0; UsedBytes = 0; AvailableBytes = 0; AvailablePercent = 0; Container = "" }
    }

    $instanceId = $instance[0].InstanceId
    $parameters = @{
        commands = @(
            'free -b | awk ''/^Mem:/ {print $2, $3, $7}''',
            'docker stats --no-stream --format "{{.Name}} {{.MemUsage}}"'
        )
    } | ConvertTo-Json -Compress

    try {
        $command = Invoke-AwsJson @(
            "ssm", "send-command",
            "--document-name", "AWS-RunShellScript",
            "--instance-ids", $instanceId,
            "--parameters", $parameters,
            "--comment", "Read-only capacity audit: $Label",
            "--region", $R,
            "--output", "json"
        )
        $commandId = "$($command.Command.CommandId)"
        $invocation = $null
        for ($attempt = 0; $attempt -lt 20; $attempt++) {
            Start-Sleep -Seconds 2
            try {
                $invocation = Invoke-AwsJson @(
                    "ssm", "get-command-invocation",
                    "--command-id", $commandId,
                    "--instance-id", $instanceId,
                    "--region", $R,
                    "--output", "json"
                )
            } catch {
                continue
            }
            if ($invocation.Status -notin @("Pending", "InProgress", "Delayed")) { break }
        }
        if (-not $invocation -or $invocation.Status -ne "Success") {
            return [PSCustomObject]@{ Status = "unavailable"; TotalBytes = 0; UsedBytes = 0; AvailableBytes = 0; AvailablePercent = 0; Container = "" }
        }
        $output = "$($invocation.StandardOutputContent)"
        if ($output -notmatch '(?m)^(\d+)\s+(\d+)\s+(\d+)\s*$') {
            return [PSCustomObject]@{ Status = "parse-failed"; TotalBytes = 0; UsedBytes = 0; AvailableBytes = 0; AvailablePercent = 0; Container = $output.Trim() }
        }
        $totalBytes = [double]$matches[1]
        $usedBytes = [double]$matches[2]
        $availableBytes = [double]$matches[3]
        $containerLine = @($output -split "`r?`n" | Where-Object { $_ -and $_ -notmatch '^\d+\s+\d+\s+\d+$' } | Select-Object -First 1)
        return [PSCustomObject]@{
            Status = "measured"
            InstanceId = $instanceId
            TotalBytes = $totalBytes
            UsedBytes = $usedBytes
            AvailableBytes = $availableBytes
            AvailablePercent = if ($totalBytes -gt 0) { [Math]::Round(($availableBytes / $totalBytes) * 100, 1) } else { 0 }
            Container = if ($containerLine.Count -gt 0) { "$($containerLine[0])" } else { "" }
        }
    } catch {
        return [PSCustomObject]@{ Status = "unavailable"; TotalBytes = 0; UsedBytes = 0; AvailableBytes = 0; AvailablePercent = 0; Container = "" }
    }
}

function Convert-BytesToGiB {
    param($Bytes)
    if ($null -eq $Bytes) { return "n/a" }
    return "{0:N2} GiB" -f ((Convert-ToDouble $Bytes) / 1GB)
}

function Format-MetricNumber {
    param($Value, [string]$Suffix = "")
    if ($null -eq $Value) { return "n/a" }
    return ("{0:N2}{1}" -f (Convert-ToDouble $Value), $Suffix)
}

function Get-Ec2HourlyPrice {
    param([string]$InstanceType)
    try {
        $res = Invoke-AwsJson @(
            "pricing", "get-products",
            "--service-code", "AmazonEC2",
            "--filters",
            "Type=TERM_MATCH,Field=location,Value=Asia Pacific (Seoul)",
            "Type=TERM_MATCH,Field=instanceType,Value=$InstanceType",
            "Type=TERM_MATCH,Field=operatingSystem,Value=Linux",
            "Type=TERM_MATCH,Field=tenancy,Value=Shared",
            "Type=TERM_MATCH,Field=preInstalledSw,Value=NA",
            "Type=TERM_MATCH,Field=capacitystatus,Value=Used",
            "--region", "us-east-1",
            "--max-results", "100",
            "--output", "json"
        )
        $prices = @()
        foreach ($productJson in @($res.PriceList)) {
            $product = $productJson | ConvertFrom-Json
            foreach ($term in @($product.terms.OnDemand.PSObject.Properties.Value)) {
                foreach ($dimension in @($term.priceDimensions.PSObject.Properties.Value)) {
                    if ($dimension.unit -eq "Hrs") {
                        $price = Convert-ToDouble $dimension.pricePerUnit.USD
                        if ($price -gt 0) { $prices += $price }
                    }
                }
            }
        }
        return @($prices | Sort-Object | Select-Object -First 1)[0]
    } catch {
        return 0.0
    }
}

Write-Host ""
Write-Host "=== V1 Cost/Waste Audit (read-only) ===" -ForegroundColor Cyan
Write-Host "  Region: $R  VpcId: $VpcId  Usage windows: $RecentDays/$UsageDays days" -ForegroundColor Gray

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
$apiActualType = Get-LatestLaunchTemplateType $script:ApiLaunchTemplateName
$messagingActualType = Get-LatestLaunchTemplateType $script:MessagingLaunchTemplateName
$aiActualType = Get-LatestLaunchTemplateType $script:AiLaunchTemplateName
$toolsActualType = Get-LatestLaunchTemplateType $script:ToolsLaunchTemplateName

Write-Host "  Collecting $RecentDays/$UsageDays-day CloudWatch capacity history..." -ForegroundColor Gray
$apiCpu = Get-CloudWatchMetricSummary "AWS/EC2" "CPUUtilization" "AutoScalingGroupName" $script:ApiASGName
$apiStatus = Get-CloudWatchMetricSummary "AWS/EC2" "StatusCheckFailed" "AutoScalingGroupName" $script:ApiASGName
$messagingCpu = Get-CloudWatchMetricSummary "AWS/EC2" "CPUUtilization" "AutoScalingGroupName" $script:MessagingASGName
$messagingStatus = Get-CloudWatchMetricSummary "AWS/EC2" "StatusCheckFailed" "AutoScalingGroupName" $script:MessagingASGName
$aiCpu = Get-CloudWatchMetricSummary "AWS/EC2" "CPUUtilization" "AutoScalingGroupName" $script:AiASGName
$toolsCpu = Get-CloudWatchMetricSummary "AWS/EC2" "CPUUtilization" "AutoScalingGroupName" $script:ToolsASGName

$apiMemory = Get-AsgLiveMemory $apiAsg "api"
$messagingMemory = Get-AsgLiveMemory $messagingAsg "messaging"

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

$rdsCpu = Get-CloudWatchMetricSummary "AWS/RDS" "CPUUtilization" "DBInstanceIdentifier" $script:RdsDbIdentifier
$rdsConnections = Get-CloudWatchMetricSummary "AWS/RDS" "DatabaseConnections" "DBInstanceIdentifier" $script:RdsDbIdentifier
$rdsFreeMemory = Get-CloudWatchMetricSummary "AWS/RDS" "FreeableMemory" "DBInstanceIdentifier" $script:RdsDbIdentifier
$rdsSwap = Get-CloudWatchMetricSummary "AWS/RDS" "SwapUsage" "DBInstanceIdentifier" $script:RdsDbIdentifier
$rdsCredits = Get-CloudWatchMetricSummary "AWS/RDS" "CPUCreditBalance" "DBInstanceIdentifier" $script:RdsDbIdentifier

$redisClusterId = if ($redisCluster) { "$($redisCluster.CacheClusterId)" } else { "$($script:RedisReplicationGroupId)-001" }
$redisCpu = Get-CloudWatchMetricSummary "AWS/ElastiCache" "CPUUtilization" "CacheClusterId" $redisClusterId
$redisEngineCpu = Get-CloudWatchMetricSummary "AWS/ElastiCache" "EngineCPUUtilization" "CacheClusterId" $redisClusterId
$redisDatabaseMemory = Get-CloudWatchMetricSummary "AWS/ElastiCache" "DatabaseMemoryUsagePercentage" "CacheClusterId" $redisClusterId
$redisFreeMemory = Get-CloudWatchMetricSummary "AWS/ElastiCache" "FreeableMemory" "CacheClusterId" $redisClusterId
$redisConnections = Get-CloudWatchMetricSummary "AWS/ElastiCache" "CurrConnections" "CacheClusterId" $redisClusterId
$redisEvictions = Get-CloudWatchMetricSummary "AWS/ElastiCache" "Evictions" "CacheClusterId" $redisClusterId
$redisCredits = Get-CloudWatchMetricSummary "AWS/ElastiCache" "CPUCreditBalance" "CacheClusterId" $redisClusterId
$redisBytes = Get-CloudWatchMetricSummary "AWS/ElastiCache" "BytesUsedForCache" "CacheClusterId" $redisClusterId

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
$queueUsageRows = @(
    [PSCustomObject]@{
        Label = "Messaging"
        Metric = Get-CloudWatchMetricSummary "AWS/SQS" "NumberOfMessagesSent" "QueueName" $script:MessagingSqsQueueName
    },
    [PSCustomObject]@{
        Label = "AI"
        Metric = Get-CloudWatchMetricSummary "AWS/SQS" "NumberOfMessagesSent" "QueueName" $script:AiSqsQueueName
    },
    [PSCustomObject]@{
        Label = "Tools"
        Metric = Get-CloudWatchMetricSummary "AWS/SQS" "NumberOfMessagesSent" "QueueName" $script:ToolsSqsQueueName
    }
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
$projectCostRows = @()
$projectCostStatus = "unavailable"
$costAllocationTagStatus = "unknown"
$allocationTagRes = Invoke-AwsJson @(
    "ce", "list-cost-allocation-tags",
    "--tag-keys", "Project",
    "--region", "us-east-1",
    "--output", "json"
)
if ($allocationTagRes -and $allocationTagRes.CostAllocationTags -and $allocationTagRes.CostAllocationTags.Count -gt 0) {
    $costAllocationTagStatus = "$($allocationTagRes.CostAllocationTags[0].Status)"
}
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
if ($costAllocationTagStatus -eq "Active") {
    $projectFilter = @{
        Tags = @{
            Key = "Project"
            Values = @("academy")
            MatchOptions = @("EQUALS")
        }
    } | ConvertTo-Json -Compress
    $projectCeRes = Invoke-AwsJson @(
        "ce", "get-cost-and-usage",
        "--time-period", "Start=$($monthStart.ToString('yyyy-MM-dd')),End=$($endExclusive.ToString('yyyy-MM-dd'))",
        "--granularity", "MONTHLY",
        "--metrics", "UnblendedCost",
        "--group-by", "Type=DIMENSION,Key=SERVICE",
        "--filter", $projectFilter,
        "--region", "us-east-1",
        "--output", "json"
    )
    if ($projectCeRes -and $projectCeRes.ResultsByTime -and $projectCeRes.ResultsByTime.Count -gt 0) {
        foreach ($g in @($projectCeRes.ResultsByTime[0].Groups)) {
            $service = @($g.Keys)[0]
            $amount = Convert-ToDouble $g.Metrics.UnblendedCost.Amount
            if ($amount -gt 0.005) {
                $projectCostRows += [PSCustomObject]@{ Service = $service; Cost = $amount }
            }
        }
        $projectCostRows = @($projectCostRows | Sort-Object Cost -Descending | Select-Object -First 15)
        $projectCostStatus = if ($projectCostRows.Count -gt 0) { "ok" } else { "active; awaiting tagged cost data" }
    }
} elseif ($costAllocationTagStatus -eq "Inactive") {
    $projectCostStatus = "inactive"
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

$mediumHourly = Convert-ToDouble (Get-Ec2HourlyPrice "t4g.medium")
$smallHourly = Convert-ToDouble (Get-Ec2HourlyPrice "t4g.small")
$messagingMonthlySavings = if ($mediumHourly -gt $smallHourly -and $smallHourly -gt 0) {
    [Math]::Round(($mediumHourly - $smallHourly) * 730, 2)
} else {
    0.0
}

$messagingHasHeadroom = (
    $messagingCpu.Long.Samples -gt 0 -and
    $messagingCpu.Long.Average -lt 10 -and
    $messagingCpu.Long.Peak -lt 70 -and
    $messagingStatus.Long.Total -eq 0 -and
    $messagingMemory.Status -eq "measured" -and
    $messagingMemory.AvailablePercent -ge 50
)
$messagingDecision = if ($messagingHasHeadroom -and $messagingActualType -eq "t4g.medium") {
    "downsize to t4g.small"
} elseif ($messagingHasHeadroom -and $messagingActualType -eq "t4g.small") {
    "keep t4g.small (right-sized)"
} else {
    "keep $messagingActualType; insufficient downsize headroom"
}

$apiDecision = if ($apiCpu.Long.Peak -ge 80) {
    "keep $apiActualType; burst CPU reached $(Format-MetricNumber $apiCpu.Long.Peak '%')"
} else {
    "keep $apiActualType pending historical memory telemetry"
}
$rdsDecision = if (
    $rdsFreeMemory.Recent.Low -lt 1GB -or
    $rdsSwap.Recent.Peak -gt 0 -or
    $rdsConnections.Long.Peak -gt 400
) {
    "keep $rdsClass; memory/connection guardrail blocks another downsize"
} else {
    "review smaller class in a maintenance window"
}
$redisDecision = if (
    $redisFreeMemory.Recent.Low -lt 0.75GB -or
    $redisCredits.Recent.Low -lt 50 -or
    $redisEvictions.Long.Total -gt 0
) {
    "keep $redisType; single-node micro headroom is not proven"
} else {
    "review cache.t4g.micro in a maintenance window"
}

$actions = [System.Collections.ArrayList]::new()
if ($messagingDecision -eq "downsize to t4g.small") {
    [void]$actions.Add("Change Messaging worker from ``t4g.medium`` to ``t4g.small``; measured CPU/memory headroom supports about $('{0:N2}' -f $messagingMonthlySavings) USD/month projected compute savings.")
}
if ($costAllocationTagStatus -ne "Active") {
    [void]$actions.Add("Activate the ``Project`` cost-allocation tag and backfill ``Project=academy`` on Academy resources so future Cost Explorer totals can be isolated from the AWS account.")
}
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
if ($ecrStatus -like "failed*") {
    [void]$actions.Add("Investigate the failed ECR cleanup dry-run before any image deletion; the cleanup tool failed closed because a protected digest was not present.")
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
[void]$sb.AppendLine("**Scope:** Academy V1 production resources in ``$R``; account-wide billing is labeled separately until project-tagged cost data accumulates.")
[void]$sb.AppendLine("**Mode:** AWS describe/get/list, CloudWatch $RecentDays/$UsageDays-day history, read-only OS measurement via SSM, and cleanup dry-runs.")
[void]$sb.AppendLine('**Truth sources:** AWS actual state, `docs/ssot/params.yaml`, CloudWatch, SSM host memory, AWS Price List, Cost Explorer, AWS Budget, and cleanup dry-runs.')
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Confirmed Facts")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Check | Result | Disposition |")
[void]$sb.AppendLine("|-------|--------|-------------|")
Add-TableRow $sb @("AWS Budget", $budgetLine, $budgetStatus)
Add-TableRow $sb @("Cost Explorer (AWS account)", "$costStatus; period $($monthStart.ToString('yyyy-MM-dd')) through $($endExclusive.AddDays(-1).ToString('yyyy-MM-dd'))", "account-wide monthly-to-date; not Academy-only")
Add-TableRow $sb @("Project cost-allocation tag", "Project status=$costAllocationTagStatus; Academy tagged-cost status=$projectCostStatus", $(if ($costAllocationTagStatus -eq "Active") { "future Academy isolation enabled" } else { "activation required" }))
Add-TableRow $sb @(
    "ECR cleanup dry-run",
    "$ecrImages image(s), $ecrGb GB reclaimable, status=$ecrStatus",
    $(if ($ecrStatus -eq "skipped") {
        "skipped"
    } elseif ($ecrStatus -like "failed*") {
        "blocked; investigate protected digest before deletion"
    } elseif ($ecrImages -gt 0) {
        "cleanup candidate"
    } else {
        "no ECR deletion needed"
    })
)
Add-TableRow $sb @("Batch jobdef cleanup dry-run", "keep=$batchKeep, drop=$batchDrop, status=$batchStatus", $(if ($batchStatus -eq "skipped") { "skipped" } elseif ($batchDrop -gt 0) { "cleanup candidate" } else { "no deregistration needed" }))
Add-TableRow $sb @("RDS class", "$rdsClass, status=$rdsStatus, pending=$rdsPending", $(if ($rdsClass -eq $script:RdsInstanceClass) { "matches SSOT" } else { "class drift" }))
Add-TableRow $sb @("Redis node", "$redisType, status=$redisStatus", $(if ($redisType -eq $script:RedisNodeType) { "matches SSOT" } else { "node type drift" }))
Add-TableRow $sb @("Running EC2 in academy VPC", "$($runningInstances.Count)", "API/Messaging warm baseline plus active worker/batch bursts")
Add-TableRow $sb @("NAT Gateway", "$natCount available", $(if ($natCount -eq 0) { "matches NAT-off posture" } else { "review recurring VPC cost" }))
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Capacity SSOT vs Actual")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Component | SSOT | Actual | Disposition |")
[void]$sb.AppendLine("|-----------|------|--------|-------------|")
Add-TableRow $sb @("API ASG", "$($apiSummary.Ssot), type=$($script:ApiInstanceType)", "$($apiSummary.Actual), type=$apiActualType", $apiSummary.Disposition)
Add-TableRow $sb @("Messaging worker ASG", "$($messagingSummary.Ssot), type=$($script:MessagingInstanceType)", "$($messagingSummary.Actual), type=$messagingActualType", $messagingSummary.Disposition)
Add-TableRow $sb @("AI worker ASG", "$($aiSummary.Ssot), type=$($script:AiInstanceType)", "$($aiSummary.Actual), type=$aiActualType", $aiSummary.Disposition)
Add-TableRow $sb @("Tools worker ASG", "$($toolsSummary.Ssot), type=$($script:ToolsInstanceType)", "$($toolsSummary.Actual), type=$toolsActualType", $toolsSummary.Disposition)
Add-TableRow $sb @("Video Batch CE", $videoCeSummary.Ssot, $videoCeSummary.Actual, $videoCeSummary.Disposition)
Add-TableRow $sb @("Video Ops CE", $opsCeSummary.Ssot, $opsCeSummary.Actual, $opsCeSummary.Disposition)
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Cumulative Usage Evidence")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Component / signal | Last $RecentDays days | Last $UsageDays days | Interpretation |")
[void]$sb.AppendLine("|--------------------|----------------------|---------------------|----------------|")
Add-TableRow $sb @(
    "API CPU",
    "avg=$(Format-MetricNumber $apiCpu.Recent.Average '%'), peak=$(Format-MetricNumber $apiCpu.Recent.Peak '%')",
    "avg=$(Format-MetricNumber $apiCpu.Long.Average '%'), peak=$(Format-MetricNumber $apiCpu.Long.Peak '%')",
    $apiDecision
)
Add-TableRow $sb @(
    "API live memory",
    "used=$(Convert-BytesToGiB $apiMemory.UsedBytes), available=$(Convert-BytesToGiB $apiMemory.AvailableBytes) ($($apiMemory.AvailablePercent)%), $($apiMemory.Status)",
    "historical OS memory unavailable",
    $apiMemory.Container
)
Add-TableRow $sb @(
    "Messaging CPU",
    "avg=$(Format-MetricNumber $messagingCpu.Recent.Average '%'), peak=$(Format-MetricNumber $messagingCpu.Recent.Peak '%')",
    "avg=$(Format-MetricNumber $messagingCpu.Long.Average '%'), peak=$(Format-MetricNumber $messagingCpu.Long.Peak '%')",
    $messagingDecision
)
Add-TableRow $sb @(
    "Messaging live memory",
    "used=$(Convert-BytesToGiB $messagingMemory.UsedBytes), available=$(Convert-BytesToGiB $messagingMemory.AvailableBytes) ($($messagingMemory.AvailablePercent)%), $($messagingMemory.Status)",
    "historical OS memory unavailable",
    $messagingMemory.Container
)
Add-TableRow $sb @(
    "AI CPU",
    "avg=$(Format-MetricNumber $aiCpu.Recent.Average '%'), peak=$(Format-MetricNumber $aiCpu.Recent.Peak '%')",
    "avg=$(Format-MetricNumber $aiCpu.Long.Average '%'), peak=$(Format-MetricNumber $aiCpu.Long.Peak '%')",
    "keep scale-to-zero; active bursts need current CPU class"
)
Add-TableRow $sb @(
    "Tools CPU",
    "avg=$(Format-MetricNumber $toolsCpu.Recent.Average '%'), peak=$(Format-MetricNumber $toolsCpu.Recent.Peak '%')",
    "avg=$(Format-MetricNumber $toolsCpu.Long.Average '%'), peak=$(Format-MetricNumber $toolsCpu.Long.Peak '%')",
    "keep scale-to-zero t4g.small"
)
foreach ($usage in $queueUsageRows) {
    Add-TableRow $sb @(
        "$($usage.Label) SQS sent",
        "$('{0:N0}' -f $usage.Metric.Recent.Total) messages",
        "$('{0:N0}' -f $usage.Metric.Long.Total) messages",
        "cumulative CloudWatch Sum"
    )
}
Add-TableRow $sb @(
    "RDS CPU / connections",
    "CPU avg=$(Format-MetricNumber $rdsCpu.Recent.Average '%'), peak=$(Format-MetricNumber $rdsCpu.Recent.Peak '%'); connections peak=$('{0:N0}' -f $rdsConnections.Recent.Peak)",
    "CPU avg=$(Format-MetricNumber $rdsCpu.Long.Average '%'), peak=$(Format-MetricNumber $rdsCpu.Long.Peak '%'); connections peak=$('{0:N0}' -f $rdsConnections.Long.Peak)",
    $rdsDecision
)
Add-TableRow $sb @(
    "RDS memory / credits",
    "free low=$(Convert-BytesToGiB $rdsFreeMemory.Recent.Low), swap peak=$(Convert-BytesToGiB $rdsSwap.Recent.Peak), credit low=$(Format-MetricNumber $rdsCredits.Recent.Low)",
    "free low=$(Convert-BytesToGiB $rdsFreeMemory.Long.Low), swap peak=$(Convert-BytesToGiB $rdsSwap.Long.Peak), credit low=$(Format-MetricNumber $rdsCredits.Long.Low)",
    "memory guardrail blocks db.t4g.small"
)
Add-TableRow $sb @(
    "Redis CPU / dataset",
    "host peak=$(Format-MetricNumber $redisCpu.Recent.Peak '%'), engine peak=$(Format-MetricNumber $redisEngineCpu.Recent.Peak '%'), dataset peak=$(Convert-BytesToGiB $redisBytes.Recent.Peak)",
    "host peak=$(Format-MetricNumber $redisCpu.Long.Peak '%'), engine peak=$(Format-MetricNumber $redisEngineCpu.Long.Peak '%'), dataset peak=$(Convert-BytesToGiB $redisBytes.Long.Peak)",
    $redisDecision
)
Add-TableRow $sb @(
    "Redis memory / connections",
    "DB memory peak=$(Format-MetricNumber $redisDatabaseMemory.Recent.Peak '%'), free low=$(Convert-BytesToGiB $redisFreeMemory.Recent.Low), connections peak=$('{0:N0}' -f $redisConnections.Recent.Peak)",
    "DB memory peak=$(Format-MetricNumber $redisDatabaseMemory.Long.Peak '%'), free low=$(Convert-BytesToGiB $redisFreeMemory.Long.Low), connections peak=$('{0:N0}' -f $redisConnections.Long.Peak), evictions=$('{0:N0}' -f $redisEvictions.Long.Total)",
    "single-node restart and low free-memory interval block micro"
)
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Right-Sizing Decisions")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Component | Decision | Evidence status |")
[void]$sb.AppendLine("|-----------|----------|-----------------|")
Add-TableRow $sb @("API", $apiDecision, "confirmed CloudWatch history + current SSM memory")
Add-TableRow $sb @("Messaging", $messagingDecision, "confirmed CloudWatch history + current SSM memory")
Add-TableRow $sb @("AI / Tools", "retain current scale-to-zero classes", "confirmed burst CPU + zero idle capacity")
Add-TableRow $sb @("RDS", $rdsDecision, "confirmed CloudWatch memory, swap, credits, connections")
Add-TableRow $sb @("Redis", $redisDecision, "confirmed CloudWatch dataset, memory, credits, connections, evictions")
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
[void]$sb.AppendLine("## Cost Explorer Snapshot - AWS Account Wide")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("Time period: $($monthStart.ToString('yyyy-MM-dd')) through $($endExclusive.AddDays(-1).ToString('yyyy-MM-dd')), unblended cost, estimated. This table is context only and must not be reported as Academy-only cost.")
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
[void]$sb.AppendLine("## Cost Explorer Snapshot - Academy Project Tag")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Service | Cost |")
[void]$sb.AppendLine("|---------|------|")
if ($projectCostRows.Count -eq 0) {
    Add-TableRow $sb @($projectCostStatus, "0.00 USD")
} else {
    foreach ($row in $projectCostRows) {
        Add-TableRow $sb @($row.Service, "$('{0:N2}' -f $row.Cost) USD")
    }
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Projections")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Projection | Amount | Basis |")
[void]$sb.AppendLine("|------------|--------|-------|")
Add-TableRow $sb @(
    "Messaging t4g.medium -> t4g.small",
    "$('{0:N2}' -f $messagingMonthlySavings) USD/month",
    "Seoul Linux on-demand price difference x 730 hours; excludes unchanged EBS/public IPv4/tax"
)
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Unverified")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Item | Status |")
[void]$sb.AppendLine("|------|--------|")
Add-TableRow $sb @("EC2 historical OS memory", "CloudWatch Agent memory metrics are not installed; SSM values are current point-in-time measurements only.")
Add-TableRow $sb @("Realized billing reduction", "Projection remains unverified until the next complete Cost Explorer billing interval after deployment.")
Add-TableRow $sb @("Academy historical tagged cost", "Cost allocation cannot be reconstructed before the Project tag activation date; only future tagged charges can be isolated.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## Policy Conflicts")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| Alternative | Conflict / disposition |")
[void]$sb.AppendLine("|-------------|------------------------|")
Add-TableRow $sb @("API scale-to-zero or smaller class", "Rejected: public API warm-baseline policy plus observed CPU burst near saturation.")
Add-TableRow $sb @("Messaging scale-to-zero", "Rejected: account recovery and Alimtalk latency require one warm worker; instance class can still be reduced.")
Add-TableRow $sb @("RDS / Redis further downsize", "Rejected for this pass: observed memory/credit lows and single-node restart risk conflict with stability guardrails.")
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
Add-TableRow $sb @("Messaging worker warm baseline", "kept at one ``$($script:MessagingInstanceType)``; account recovery and Alimtalk wait paths should not cold-start.")
Add-TableRow $sb @("AI/Tools workers", "scale-to-zero policy retained; queue alarms/API wake-up own burst scale-out.")
Add-TableRow $sb @("Standard video encoding", "Spot Batch CE retained; paid encode tests are not submitted by this audit.")
Add-TableRow $sb @("RDS/Redis", "current classes retained because this audit found memory/credit guardrails against another downsize.")

if (-not (Test-Path $ReportsDir)) { New-Item -ItemType Directory -Path $ReportsDir -Force | Out-Null }
[void](New-Item -ItemType Directory -Path $HistoryDir -Force)
$reportText = $sb.ToString().TrimEnd() + [Environment]::NewLine
$historyPath = Join-Path $HistoryDir ("{0}-cost-waste-audit.md" -f $now.ToString("yyyyMMdd-HHmmss"))
[System.IO.File]::WriteAllText(
    $ReportPath,
    $reportText,
    [System.Text.UTF8Encoding]::new($false)
)
[System.IO.File]::WriteAllText(
    $historyPath,
    $reportText,
    [System.Text.UTF8Encoding]::new($false)
)

Write-Host "  cost-waste-audit.latest.md: $ReportPath" -ForegroundColor Green
Write-Host "  history snapshot: $historyPath" -ForegroundColor Green
Write-Host "=== Done ===" -ForegroundColor Cyan
