# Read-only production canary for Academy operations.
#
# Quick: public edge + core AWS + remote DB invariants on one API instance.
# PostDeploy: strict warnings, remote checks on every live API instance, and worker infra checks.
# Deep: PostDeploy plus the same worker infra checks for manual deep audits.

param(
    [ValidateSet("Quick", "PostDeploy", "Deep")][string]$Mode = "Quick",
    [string]$AwsProfile = "default",
    [string]$Env = "prod",
    [string]$ApiBaseUrl = "",
    [string]$FrontBaseUrl = "",
    [int]$TimeoutSec = 15,
    [int]$RemoteTimeoutSec = 180,
    [int]$DlqFailThreshold = 5,
    [switch]$SkipAws,
    [switch]$SkipRemoteDjango,
    [switch]$StrictWarnings,
    [switch]$WriteReport,
    [string]$ReportPath = "docs/reports/production-canary.latest.md",
    [switch]$Json
)

$ErrorActionPreference = "Continue"
$OutputEncoding = [System.Text.Encoding]::UTF8
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path

. (Join-Path $ScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) {
    $env:AWS_DEFAULT_REGION = "ap-northeast-2"
}
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")
. (Join-Path $ScriptRoot "core\remote.ps1")

$null = Load-SSOT -Env $Env
$Region = if ($script:Region) { $script:Region } else { $env:AWS_DEFAULT_REGION }

function ConvertTo-CanaryUrl {
    param([string]$Value)
    $trimmed = if ($Value) { $Value.Trim() } else { "" }
    if (-not $trimmed) { return "" }
    if ($trimmed -match '^https?://') { return $trimmed.TrimEnd("/") }
    return "https://$($trimmed.TrimEnd("/"))"
}

if (-not $ApiBaseUrl -and $script:FrontDomainApi) { $ApiBaseUrl = ConvertTo-CanaryUrl $script:FrontDomainApi }
if (-not $ApiBaseUrl) { $ApiBaseUrl = "https://api.hakwonplus.com" }
if (-not $FrontBaseUrl -and $script:FrontDomainApp) { $FrontBaseUrl = ConvertTo-CanaryUrl $script:FrontDomainApp }
if (-not $FrontBaseUrl) { $FrontBaseUrl = "https://hakwonplus.com" }
$ApiBaseUrl = (ConvertTo-CanaryUrl $ApiBaseUrl)
$FrontBaseUrl = (ConvertTo-CanaryUrl $FrontBaseUrl)

$warningsAreFailures = $StrictWarnings -or $Mode -in @("PostDeploy", "Deep")
$script:CanaryRows = [System.Collections.ArrayList]::new()

function Write-CanaryHost {
    param(
        [string]$Message = "",
        [System.ConsoleColor]$ForegroundColor = [System.ConsoleColor]::White,
        [switch]$HasColor
    )
    if ($Json) { return }
    if ($HasColor) {
        Write-Host $Message -ForegroundColor $ForegroundColor
    } else {
        Write-Host $Message
    }
}

function Invoke-CanaryAwsJson {
    param(
        [Parameter(Mandatory = $true)][string[]]$ArgsArray,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $result = Invoke-AwsJson $ArgsArray
    if ($null -eq $result) {
        throw "$Description returned no JSON; AWS CLI call failed, was denied, or returned invalid JSON."
    }
    return $result
}

function Add-CanaryResult {
    param([string]$Stage, [string]$Name, [string]$Severity, [bool]$Ok, [string]$Detail)
    $status = if ($Ok) { "PASS" } elseif ($Severity -eq "warning") { "WARN" } else { "FAIL" }
    $color = switch ($status) { "PASS" { "Green" } "WARN" { "Yellow" } default { "Red" } }
    Write-CanaryHost ("  [{0}] {1} - {2}" -f $status, $Name, $Detail) -ForegroundColor $color -HasColor
    [void]$script:CanaryRows.Add([PSCustomObject]@{
        Stage = $Stage
        Name = $Name
        Severity = $Severity
        Status = $status
        Detail = $Detail
    })
}

function Get-HttpStatus {
    param(
        [string]$Url,
        [string]$Method = "GET",
        [hashtable]$Headers = @{},
        [string]$Body = ""
    )
    try {
        $args = @{
            Uri = $Url
            Method = $Method
            TimeoutSec = $TimeoutSec
            UseBasicParsing = $true
            Headers = $Headers
            ErrorAction = "Stop"
        }
        if ($Body -ne "") {
            $args["Body"] = $Body
            $args["ContentType"] = "application/json"
        }
        $resp = Invoke-WebRequest @args
        return [PSCustomObject]@{ Status = [int]$resp.StatusCode; Error = ""; Length = ($resp.Content | Out-String).Length }
    } catch {
        $status = 0
        if ($_.Exception.Response) {
            try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = 0 }
        }
        return [PSCustomObject]@{ Status = $status; Error = $_.Exception.Message; Length = 0 }
    }
}

function Test-HttpCanary {
    param(
        [string]$Name,
        [string]$Url,
        [int]$MinStatus = 200,
        [int]$MaxStatus = 399,
        [string]$Method = "GET",
        [hashtable]$Headers = @{},
        [string]$Body = ""
    )
    $result = Get-HttpStatus -Url $Url -Method $Method -Headers $Headers -Body $Body
    $ok = $result.Status -ge $MinStatus -and $result.Status -le $MaxStatus
    $detail = if ($ok) { "HTTP $($result.Status)" } else { "HTTP $($result.Status) $($result.Error)" }
    Add-CanaryResult -Stage "HTTP" -Name $Name -Severity "error" -Ok $ok -Detail $detail
}

function Test-ScalingControlAlarm($alarm) {
    $actions = (@($alarm.AlarmActions) -join " ")
    $isSqsDepthAlarm = (
        $alarm.Namespace -eq "AWS/SQS" -and
        $alarm.MetricName -eq "ApproximateNumberOfMessagesVisible"
    )
    if (
        $isSqsDepthAlarm -and
        $alarm.AlarmName -eq "ai-worker-queue-low" -and
        $alarm.ComparisonOperator -match "^LessThan" -and
        -not $alarm.ActionsEnabled
    ) {
        return $true
    }
    return (
        $isSqsDepthAlarm -and
        $alarm.ComparisonOperator -match "^LessThan" -and
        $alarm.ActionsEnabled -and
        $actions -match "scalingPolicy"
    )
}

function Test-AsgCanary {
    param([string]$Name, [string]$AsgName, [string]$Severity = "warning")
    if (-not $AsgName) {
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity $Severity -Ok $false -Detail "ASG name missing"
        return
    }
    try {
        $asgJson = Invoke-CanaryAwsJson -Description "describe ASG $AsgName" -ArgsArray @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $AsgName, "--region", $Region, "--output", "json")
        $asg = $asgJson.AutoScalingGroups[0]
        if (-not $asg) {
            Add-CanaryResult -Stage "AWS" -Name $Name -Severity $Severity -Ok $false -Detail "ASG not found: $AsgName"
            return
        }
        $healthy = @($asg.Instances | Where-Object { $_.HealthStatus -eq "Healthy" -and $_.LifecycleState -eq "InService" }).Count
        $required = if ($Mode -in @("PostDeploy", "Deep")) { [int]$asg.DesiredCapacity } else { [int]$asg.MinSize }
        $ok = $healthy -ge $required
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity $Severity -Ok $ok -Detail "$healthy healthy / min=$($asg.MinSize) desired=$($asg.DesiredCapacity) max=$($asg.MaxSize)"
    } catch {
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity $Severity -Ok $false -Detail $_.Exception.Message
    }
}

function Test-SqsCanary {
    param([string]$Name, [string]$QueueName, [switch]$Dlq)
    if (-not $QueueName) {
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity "error" -Ok $false -Detail "queue name missing"
        return
    }
    try {
        $queue = Invoke-CanaryAwsJson -Description "get SQS queue URL $QueueName" -ArgsArray @("sqs", "get-queue-url", "--queue-name", $QueueName, "--region", $Region, "--output", "json")
        if (-not $queue.QueueUrl) { throw "SQS queue URL missing for $QueueName" }
        $attrs = Invoke-CanaryAwsJson -Description "get SQS queue attributes $QueueName" -ArgsArray @(
            "sqs", "get-queue-attributes",
            "--queue-url", $queue.QueueUrl,
            "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
            "--region", $Region,
            "--output", "json"
        )
        if ($null -eq $attrs.Attributes) { throw "SQS attributes missing for $QueueName" }
        $visible = [int]$attrs.Attributes.ApproximateNumberOfMessages
        $inFlight = [int]$attrs.Attributes.ApproximateNumberOfMessagesNotVisible
        $total = $visible + $inFlight
        if ($Dlq) {
            $severity = if ($total -ge $DlqFailThreshold) { "error" } else { "warning" }
            Add-CanaryResult -Stage "AWS" -Name $Name -Severity $severity -Ok ($total -eq 0) -Detail "visible=$visible in_flight=$inFlight threshold=$DlqFailThreshold"
        } else {
            Add-CanaryResult -Stage "AWS" -Name $Name -Severity "warning" -Ok ($visible -le 100) -Detail "visible=$visible in_flight=$inFlight"
        }
    } catch {
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity "error" -Ok $false -Detail $_.Exception.Message
    }
}

function Test-BatchQueueCanary {
    param([string]$Name, [string]$QueueName)
    if (-not $QueueName) { return }
    try {
        $queue = Invoke-CanaryAwsJson -Description "describe Batch queue $QueueName" -ArgsArray @("batch", "describe-job-queues", "--job-queues", $QueueName, "--region", $Region, "--output", "json")
        $row = $queue.jobQueues[0]
        $ok = $row.state -eq "ENABLED" -and $row.status -eq "VALID"
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity "error" -Ok $ok -Detail "$($row.state)/$($row.status)"
    } catch {
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity "warning" -Ok $false -Detail $_.Exception.Message
    }
}

function Test-BatchCeCanary {
    param([string]$Name, [string]$CeName)
    if (-not $CeName) { return }
    try {
        $ce = Invoke-CanaryAwsJson -Description "describe Batch compute environment $CeName" -ArgsArray @("batch", "describe-compute-environments", "--compute-environments", $CeName, "--region", $Region, "--output", "json")
        $row = $ce.computeEnvironments[0]
        $ok = $row.state -eq "ENABLED" -and $row.status -eq "VALID"
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity "error" -Ok $ok -Detail "$($row.state)/$($row.status)"
    } catch {
        Add-CanaryResult -Stage "AWS" -Name $Name -Severity "warning" -Ok $false -Detail $_.Exception.Message
    }
}

function Add-RemoteResult {
    param([string]$Name, $Results)
    foreach ($result in @($Results)) {
        $ok = $result.Status -eq "Success" -and [int]$result.ResponseCode -eq 0
        $detail = "instance=$($result.InstanceId) status=$($result.Status) rc=$($result.ResponseCode)"
        if (-not $ok -and $result.StandardErrorContent) {
            $detail += " stderr=$($result.StandardErrorContent.Trim())"
        }
        Add-CanaryResult -Stage "REMOTE" -Name $Name -Severity "error" -Ok $ok -Detail $detail
        if ($result.StandardOutputContent) {
            Write-CanaryHost $result.StandardOutputContent.Trim() -ForegroundColor Gray -HasColor
        }
    }
}

Write-CanaryHost ""
Write-CanaryHost "=== Academy Production Canary ===" -ForegroundColor Cyan -HasColor
Write-CanaryHost "Mode:  $Mode"
Write-CanaryHost "API:   $ApiBaseUrl"
Write-CanaryHost "Front: $FrontBaseUrl"
Write-CanaryHost "AWS:   profile=$env:AWS_PROFILE region=$Region"
Write-CanaryHost ""

Write-CanaryHost "[1/3] Public HTTP edge" -ForegroundColor Cyan -HasColor
Test-HttpCanary -Name "api_healthz" -Url "$ApiBaseUrl/healthz"
Test-HttpCanary -Name "api_health" -Url "$ApiBaseUrl/health"
Test-HttpCanary -Name "api_readyz" -Url "$ApiBaseUrl/readyz"
Test-HttpCanary -Name "front_root" -Url "$FrontBaseUrl/"
Test-HttpCanary -Name "front_promo" -Url "$FrontBaseUrl/promo"
Test-HttpCanary -Name "api_program_tenant_healthy" -Url "$ApiBaseUrl/api/v1/core/program/" -MaxStatus 299 -Headers @{ "X-Tenant-Code" = "hakwonplus" }
Test-HttpCanary `
    -Name "api_invalid_login_no_5xx" `
    -Url "$ApiBaseUrl/api/v1/token/" `
    -Method "POST" `
    -MinStatus 400 `
    -MaxStatus 499 `
    -Headers @{ "X-Tenant-Code" = "hakwonplus" } `
    -Body (@{ username = "__canary__"; password = "__canary__"; tenant_code = "hakwonplus" } | ConvertTo-Json -Compress)
Write-CanaryHost ""

if (-not $SkipAws) {
    Write-CanaryHost "[2/3] AWS infrastructure signals" -ForegroundColor Cyan -HasColor
    try {
        $identity = Invoke-CanaryAwsJson -Description "get AWS caller identity" -ArgsArray @("sts", "get-caller-identity", "--output", "json")
        Add-CanaryResult -Stage "AWS" -Name "aws_identity" -Severity "error" -Ok ($identity -and $identity.Account) -Detail $(if ($identity) { "account=$($identity.Account)" } else { "unavailable" })
    } catch {
        Add-CanaryResult -Stage "AWS" -Name "aws_identity" -Severity "error" -Ok $false -Detail $_.Exception.Message
    }

    Test-AsgCanary -Name "api_asg" -AsgName $script:ApiASGName -Severity "error"
    Test-AsgCanary -Name "messaging_asg" -AsgName $script:MessagingASGName -Severity "warning"
    Test-AsgCanary -Name "ai_asg" -AsgName $script:AiASGName -Severity "warning"
    Test-AsgCanary -Name "tools_asg" -AsgName $script:ToolsASGName -Severity "warning"

    if ($script:ApiTargetGroupName) {
        try {
            $tg = Invoke-CanaryAwsJson -Description "describe ALB target group $($script:ApiTargetGroupName)" -ArgsArray @("elbv2", "describe-target-groups", "--names", $script:ApiTargetGroupName, "--region", $Region, "--output", "json")
            $tgArn = $tg.TargetGroups[0].TargetGroupArn
            if (-not $tgArn) { throw "target group ARN missing" }
            $th = Invoke-CanaryAwsJson -Description "describe ALB target health $($script:ApiTargetGroupName)" -ArgsArray @("elbv2", "describe-target-health", "--target-group-arn", $tgArn, "--region", $Region, "--output", "json")
            $total = @($th.TargetHealthDescriptions).Count
            $healthy = @($th.TargetHealthDescriptions | Where-Object { $_.TargetHealth.State -eq "healthy" }).Count
            Add-CanaryResult -Stage "AWS" -Name "alb_target_health" -Severity "error" -Ok ($total -gt 0 -and $healthy -eq $total) -Detail "$healthy/$total healthy"
        } catch {
            Add-CanaryResult -Stage "AWS" -Name "alb_target_health" -Severity "error" -Ok $false -Detail $_.Exception.Message
        }
    }

    try {
        $rds = Invoke-CanaryAwsJson -Description "describe RDS $($script:RdsDbIdentifier)" -ArgsArray @("rds", "describe-db-instances", "--db-instance-identifier", $script:RdsDbIdentifier, "--region", $Region, "--output", "json")
        $rdsStatus = $rds.DBInstances[0].DBInstanceStatus
        Add-CanaryResult -Stage "AWS" -Name "rds_status" -Severity "error" -Ok ($rdsStatus -eq "available") -Detail $rdsStatus
    } catch {
        Add-CanaryResult -Stage "AWS" -Name "rds_status" -Severity "error" -Ok $false -Detail $_.Exception.Message
    }

    try {
        $redis = Invoke-CanaryAwsJson -Description "describe Redis $($script:RedisReplicationGroupId)" -ArgsArray @("elasticache", "describe-replication-groups", "--replication-group-id", $script:RedisReplicationGroupId, "--region", $Region, "--output", "json")
        $redisStatus = $redis.ReplicationGroups[0].Status
        Add-CanaryResult -Stage "AWS" -Name "redis_status" -Severity "error" -Ok ($redisStatus -eq "available") -Detail $redisStatus
    } catch {
        Add-CanaryResult -Stage "AWS" -Name "redis_status" -Severity "warning" -Ok $false -Detail $_.Exception.Message
    }

    Test-SqsCanary -Name "messaging_queue" -QueueName $script:MessagingSqsQueueName
    Test-SqsCanary -Name "messaging_dlq" -QueueName "$($script:MessagingSqsQueueName)-dlq" -Dlq
    Test-SqsCanary -Name "ai_queue" -QueueName $script:AiSqsQueueName
    Test-SqsCanary -Name "ai_dlq" -QueueName "$($script:AiSqsQueueName)-dlq" -Dlq
    Test-SqsCanary -Name "tools_queue" -QueueName $script:ToolsSqsQueueName
    Test-SqsCanary -Name "tools_dlq" -QueueName "$($script:ToolsSqsQueueName)-dlq" -Dlq

    try {
        $alarmsJson = Invoke-CanaryAwsJson -Description "describe CloudWatch alarms" -ArgsArray @("cloudwatch", "describe-alarms", "--state-value", "ALARM", "--region", $Region, "--output", "json")
        $alarms = @($alarmsJson.MetricAlarms)
        $serviceAlarms = @($alarms | Where-Object { -not (Test-ScalingControlAlarm $_) })
        $detail = if ($serviceAlarms.Count -eq 0) { "no service alarms" } else { (($serviceAlarms | Select-Object -ExpandProperty AlarmName) -join ", ") }
        Add-CanaryResult -Stage "AWS" -Name "cloudwatch_service_alarms" -Severity "error" -Ok ($serviceAlarms.Count -eq 0) -Detail $detail
    } catch {
        Add-CanaryResult -Stage "AWS" -Name "cloudwatch_service_alarms" -Severity "error" -Ok $false -Detail $_.Exception.Message
    }

    if ($Mode -in @("Deep", "PostDeploy")) {
        Test-BatchQueueCanary -Name "video_batch_queue" -QueueName $script:VideoQueueName
        Test-BatchCeCanary -Name "video_batch_ce" -CeName $script:VideoCEName
        Test-BatchQueueCanary -Name "video_ops_queue" -QueueName $script:OpsQueueName
        Test-BatchCeCanary -Name "video_ops_ce" -CeName $script:OpsCEName
    }
    Write-CanaryHost ""
} else {
    Write-CanaryHost "[2/3] AWS infrastructure signals skipped" -ForegroundColor Yellow -HasColor
    Write-CanaryHost ""
}

if (-not $SkipRemoteDjango) {
    Write-CanaryHost "[3/3] Remote Django invariants on live container" -ForegroundColor Cyan -HasColor
    $allInstances = $Mode -in @("PostDeploy", "Deep")
    Add-RemoteResult -Name "django_check_deploy" -Results (Invoke-ApiSsmDockerExec -Command "python manage.py check --deploy --fail-level ERROR" -TimeoutSec $RemoteTimeoutSec -AllInstances:$allInstances)
    $migrationCommand = 'python manage.py showmigrations > /tmp/academy-showmigrations.txt 2>&1; rc=$?; cat /tmp/academy-showmigrations.txt; if [ $rc -ne 0 ]; then exit $rc; fi; if grep -E "^ \[ \]" /tmp/academy-showmigrations.txt; then exit 1; fi'
    Add-RemoteResult -Name "django_migrations_applied" -Results (Invoke-ApiSsmDockerExec -Command $migrationCommand -TimeoutSec $RemoteTimeoutSec -AllInstances:$allInstances)
    $invariantCommand = "python manage.py production_canary --tenant-id 1 --tenant-code hakwonplus --indent 2"
    if ($warningsAreFailures) {
        $invariantCommand += " --fail-on-warning"
    }
    Add-RemoteResult -Name "django_production_canary" -Results (Invoke-ApiSsmDockerExec -Command $invariantCommand -TimeoutSec $RemoteTimeoutSec -AllInstances:$allInstances)
    Write-CanaryHost ""
} else {
    Write-CanaryHost "[3/3] Remote Django invariants skipped" -ForegroundColor Yellow -HasColor
    Write-CanaryHost ""
}

$failCount = @($script:CanaryRows | Where-Object { $_.Status -eq "FAIL" }).Count
$warnCount = @($script:CanaryRows | Where-Object { $_.Status -eq "WARN" }).Count
$passCount = @($script:CanaryRows | Where-Object { $_.Status -eq "PASS" }).Count
$verdict = if ($failCount -gt 0) { "FAIL" } elseif ($warnCount -gt 0) { "WARNING" } else { "PASS" }

Write-CanaryHost "=== Canary Summary ===" -ForegroundColor Cyan -HasColor
Write-CanaryHost "PASS=$passCount WARN=$warnCount FAIL=$failCount VERDICT=$verdict"

if ($WriteReport) {
    $resolvedReportPath = if ([System.IO.Path]::IsPathRooted($ReportPath)) { $ReportPath } else { Join-Path $RepoRoot $ReportPath }
    $reportDir = Split-Path -Parent $resolvedReportPath
    if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Path $reportDir -Force | Out-Null }
    $generated = Get-Date -Format "o"
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine("# Production Canary")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("**Generated:** $generated")
    [void]$sb.AppendLine("**Mode:** $Mode")
    [void]$sb.AppendLine("**Verdict:** $verdict")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("| Stage | Name | Status | Detail |")
    [void]$sb.AppendLine("|-------|------|--------|--------|")
    foreach ($row in $script:CanaryRows) {
        $detail = ([string]$row.Detail).Replace("|", "\|")
        [void]$sb.AppendLine("| $($row.Stage) | $($row.Name) | $($row.Status) | $detail |")
    }
    [System.IO.File]::WriteAllText($resolvedReportPath, $sb.ToString(), [System.Text.UTF8Encoding]::new($false))
    Write-CanaryHost "Report: $resolvedReportPath" -ForegroundColor Gray -HasColor
}

if ($Json) {
    $payload = @{
        mode = $Mode
        verdict = $verdict
        pass = $passCount
        warn = $warnCount
        fail = $failCount
        rows = @($script:CanaryRows)
    }
    Write-Output ($payload | ConvertTo-Json -Depth 5 -Compress)
}

if ($failCount -gt 0) { exit 1 }
if ($warnCount -gt 0 -and $warningsAreFailures) { exit 2 }
exit 0
