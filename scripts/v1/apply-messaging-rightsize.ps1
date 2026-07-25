# Changes only the Messaging worker instance class while preserving the exact
# immutable image/UserData from the current Launch Template latest version.
param(
    [string]$AwsProfile = "default",
    [string]$TargetInstanceType = "",
    [switch]$Plan
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
. (Join-Path $ScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\guard.ps1")
. (Join-Path $ScriptRoot "resources\cost_tags.ps1")

$script:PlanMode = $Plan
$script:DeployLockAcquired = $false
$null = Load-SSOT -Env "prod"
if (-not $TargetInstanceType) { $TargetInstanceType = $script:MessagingInstanceType }
if ($TargetInstanceType -ne $script:MessagingInstanceType) {
    throw "TargetInstanceType must match docs/ssot/params.yaml messagingWorker.instanceType ($($script:MessagingInstanceType))."
}

function Get-LatestMessagingTemplateVersion {
    $res = Invoke-AwsJson @(
        "ec2", "describe-launch-template-versions",
        "--launch-template-name", $script:MessagingLaunchTemplateName,
        "--versions", '$Latest',
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $res.LaunchTemplateVersions) {
        throw "Messaging Launch Template latest version not found: $($script:MessagingLaunchTemplateName)"
    }
    return $res.LaunchTemplateVersions[0]
}

function Assert-TemplateImageExists {
    param($TemplateVersion)
    $userData = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String("$($TemplateVersion.LaunchTemplateData.UserData)"))
    $match = [regex]::Match(
        $userData,
        '\d+\.dkr\.ecr\.[^\s"'']+/academy-messaging-worker@(sha256:[0-9a-f]{64})'
    )
    if (-not $match.Success) {
        throw "Messaging Launch Template UserData does not contain an immutable academy-messaging-worker digest."
    }
    $digest = $match.Groups[1].Value
    $image = Invoke-AwsJson @(
        "ecr", "describe-images",
        "--repository-name", $script:EcrMessagingRepo,
        "--image-ids", "imageDigest=$digest",
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $image.imageDetails) {
        throw "Messaging image digest is not present in ECR: $digest"
    }
    return "$($match.Value)"
}

function Wait-MessagingRefresh {
    param([string]$RefreshId, [int]$TimeoutSeconds = 1200)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $res = Invoke-AwsJson @(
            "autoscaling", "describe-instance-refreshes",
            "--auto-scaling-group-name", $script:MessagingASGName,
            "--instance-refresh-ids", $RefreshId,
            "--region", $script:Region,
            "--output", "json"
        )
        $refresh = @($res.InstanceRefreshes)[0]
        if (-not $refresh) { throw "Instance refresh disappeared: $RefreshId" }
        Write-Host "  Refresh $RefreshId status=$($refresh.Status) percentage=$($refresh.PercentageComplete)" -ForegroundColor Gray
        if ($refresh.Status -eq "Successful") { return }
        if ($refresh.Status -in @("Failed", "Cancelled", "RollbackFailed", "RollbackSuccessful")) {
            throw "Messaging instance refresh ended with status=$($refresh.Status): $($refresh.StatusReason)"
        }
        Start-Sleep -Seconds 15
    } while ((Get-Date) -lt $deadline)
    throw "Messaging instance refresh timed out after ${TimeoutSeconds}s: $RefreshId"
}

function Start-MessagingRefresh {
    $preferences = @{
        MinHealthyPercentage = 100
        MaxHealthyPercentage = 200
        InstanceWarmup = 120
        SkipMatching = $false
    } | ConvertTo-Json -Compress
    $res = Invoke-AwsJson @(
        "autoscaling", "start-instance-refresh",
        "--auto-scaling-group-name", $script:MessagingASGName,
        "--preferences", $preferences,
        "--region", $script:Region,
        "--output", "json"
    )
    if (-not $res.InstanceRefreshId) { throw "start-instance-refresh returned no id" }
    return "$($res.InstanceRefreshId)"
}

function Test-MessagingFleetMatchesTarget {
    $asgRes = Invoke-AwsJson @(
        "autoscaling", "describe-auto-scaling-groups",
        "--auto-scaling-group-names", $script:MessagingASGName,
        "--region", $script:Region,
        "--output", "json"
    )
    $asg = @($asgRes.AutoScalingGroups)[0]
    $inServiceIds = @($asg.Instances | Where-Object {
        $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy"
    } | ForEach-Object { $_.InstanceId })
    if ($inServiceIds.Count -lt $script:MessagingMinSize) { return $false }

    $ec2 = Invoke-AwsJson (
        @("ec2", "describe-instances", "--instance-ids") +
        $inServiceIds +
        @("--region", $script:Region, "--output", "json")
    )
    $instances = @($ec2.Reservations | ForEach-Object { $_.Instances } | ForEach-Object { $_ })
    return (
        $instances.Count -eq $inServiceIds.Count -and
        @($instances | Where-Object { $_.InstanceType -ne $TargetInstanceType }).Count -eq 0
    )
}

function Restore-MessagingTemplate {
    param([int]$SourceVersion)
    Write-Warn "Creating rollback Launch Template version from source version $SourceVersion"
    $rollback = Invoke-AwsJson @(
        "ec2", "create-launch-template-version",
        "--launch-template-name", $script:MessagingLaunchTemplateName,
        "--source-version", "$SourceVersion",
        "--version-description", "Rollback failed Messaging right-size",
        "--region", $script:Region,
        "--output", "json"
    )
    $rollbackVersion = [int]$rollback.LaunchTemplateVersion.VersionNumber
    Invoke-Aws @(
        "ec2", "modify-launch-template",
        "--launch-template-name", $script:MessagingLaunchTemplateName,
        "--default-version", "$rollbackVersion",
        "--region", $script:Region
    ) -ErrorMessage "set Messaging rollback Launch Template default" | Out-Null
    $rollbackRefreshId = Start-MessagingRefresh
    Wait-MessagingRefresh -RefreshId $rollbackRefreshId
}

function Verify-MessagingRuntime {
    $asgRes = Invoke-AwsJson @(
        "autoscaling", "describe-auto-scaling-groups",
        "--auto-scaling-group-names", $script:MessagingASGName,
        "--region", $script:Region,
        "--output", "json"
    )
    $asg = @($asgRes.AutoScalingGroups)[0]
    $inService = @($asg.Instances | Where-Object {
        $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy"
    })
    if ($inService.Count -lt $script:MessagingMinSize) {
        throw "Messaging healthy InService instances=$($inService.Count), expected at least $($script:MessagingMinSize)."
    }
    $ids = @($inService | ForEach-Object { $_.InstanceId })
    $ec2 = Invoke-AwsJson (
        @("ec2", "describe-instances", "--instance-ids") +
        $ids +
        @("--region", $script:Region, "--output", "json")
    )
    $instances = @($ec2.Reservations | ForEach-Object { $_.Instances } | ForEach-Object { $_ })
    $wrong = @($instances | Where-Object { $_.InstanceType -ne $TargetInstanceType })
    if ($wrong.Count -gt 0) {
        throw "Messaging instance type verification failed: $($wrong.InstanceId -join ', ')"
    }

    $instanceId = $ids[0]
    $parameters = @{
        commands = @(
            'free -m',
            'docker inspect academy-messaging-worker --format "{{.State.Status}} {{.Config.Image}}"',
            'docker stats --no-stream --format "{{.Name}} {{.MemUsage}} {{.MemPerc}}"'
        )
    } | ConvertTo-Json -Compress
    $command = Invoke-AwsJson @(
        "ssm", "send-command",
        "--document-name", "AWS-RunShellScript",
        "--instance-ids", $instanceId,
        "--parameters", $parameters,
        "--comment", "Verify Messaging right-size runtime",
        "--region", $script:Region,
        "--output", "json"
    )
    $commandId = "$($command.Command.CommandId)"
    $invocation = $null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 3
        try {
            $invocation = Invoke-AwsJson @(
                "ssm", "get-command-invocation",
                "--command-id", $commandId,
                "--instance-id", $instanceId,
                "--region", $script:Region,
                "--output", "json"
            )
        } catch {
            continue
        }
        if ($invocation.Status -notin @("Pending", "InProgress", "Delayed")) { break }
    }
    if (-not $invocation -or $invocation.Status -ne "Success") {
        throw "Messaging SSM runtime verification failed for $instanceId."
    }
    if ($invocation.StandardOutputContent -notmatch '(?m)^running\s+.+academy-messaging-worker@sha256:[0-9a-f]{64}\s*$') {
        throw "Messaging container is not running with an immutable image: $($invocation.StandardOutputContent)"
    }

    $queue = Invoke-AwsJson @(
        "sqs", "get-queue-attributes",
        "--queue-url", $script:MessagingSqsQueueUrl,
        "--attribute-names", "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
        "--region", $script:Region,
        "--output", "json"
    )
    Write-Host "  Messaging runtime instance: $instanceId ($TargetInstanceType)" -ForegroundColor Green
    Write-Host $invocation.StandardOutputContent.Trim() -ForegroundColor Gray
    Write-Host "  Messaging queue visible=$($queue.Attributes.ApproximateNumberOfMessages) in-flight=$($queue.Attributes.ApproximateNumberOfMessagesNotVisible)" -ForegroundColor Gray
}

$latest = Get-LatestMessagingTemplateVersion
$sourceVersion = [int]$latest.VersionNumber
$sourceType = "$($latest.LaunchTemplateData.InstanceType)"
$sourceImage = Assert-TemplateImageExists -TemplateVersion $latest

Write-Host "`n=== Messaging Worker Right-Size (Plan=$Plan) ===" -ForegroundColor Cyan
Write-Host "  LT: $($script:MessagingLaunchTemplateName) version=$sourceVersion type=$sourceType" -ForegroundColor Gray
Write-Host "  Target: $TargetInstanceType" -ForegroundColor Gray
Write-Host "  Preserved image: $sourceImage" -ForegroundColor Gray

if ($Plan) {
    Write-Host "=== Plan complete; no AWS changes ===`n" -ForegroundColor Green
    exit 0
}

try {
    Acquire-DeployLock -Reg $script:Region
    $refreshRequired = -not (Test-MessagingFleetMatchesTarget)
    if ($sourceType -ne $TargetInstanceType) {
        $override = @{ InstanceType = $TargetInstanceType } | ConvertTo-Json -Compress
        $created = Invoke-AwsJson @(
            "ec2", "create-launch-template-version",
            "--launch-template-name", $script:MessagingLaunchTemplateName,
            "--source-version", "$sourceVersion",
            "--version-description", "Messaging right-size $sourceType to $TargetInstanceType",
            "--launch-template-data", $override,
            "--region", $script:Region,
            "--output", "json"
        )
        $newVersion = [int]$created.LaunchTemplateVersion.VersionNumber
        Invoke-Aws @(
            "ec2", "modify-launch-template",
            "--launch-template-name", $script:MessagingLaunchTemplateName,
            "--default-version", "$newVersion",
            "--region", $script:Region
        ) -ErrorMessage "set Messaging Launch Template default version" | Out-Null
        $refreshRequired = $true
    }
    if ($refreshRequired) {
        try {
            $refreshId = Start-MessagingRefresh
            Wait-MessagingRefresh -RefreshId $refreshId
        } catch {
            Restore-MessagingTemplate -SourceVersion $sourceVersion
            throw
        }
    }
    Ensure-ProjectCostAllocationTags
    Verify-MessagingRuntime
    Write-Host "=== Messaging right-size applied and verified ===`n" -ForegroundColor Green
} finally {
    Release-DeployLock -Reg $script:Region
}
