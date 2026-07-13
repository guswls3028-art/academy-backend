# Pins an API/worker ASG Launch Template to the digest behind one CI sha-* tag.
# This script does not start an instance refresh; the caller must run its existing
# health/capacity gates first and start the refresh only after this command succeeds.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("api", "messaging", "ai", "tools")]
    [string]$Service,

    [ValidatePattern('^sha-(?:[0-9a-fA-F]{8,40}|[0-9a-fA-F]{40}-run-[0-9]+-[0-9]+)$')]
    [string]$ImageTag = "",

    [string]$StatePath = "",
    [string]$VerifyStatePath = "",
    [string]$RestoreStatePath = "",
    [switch]$RefreshAndVerify,

    [switch]$Ci = $false,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot

if ($Ci) {
    Remove-Item Env:AWS_PROFILE -ErrorAction SilentlyContinue
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
} elseif ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}

$script:PlanMode = $false
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "resources\worker_userdata.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")

Load-SSOT -Env prod | Out-Null
if (-not $script:EcrImmutableTagRequired -or $script:EcrUseLatestTag) {
    throw "SSOT must require immutable images and prohibit useLatestTag before ASG image pinning."
}

function Get-UserDataRuntimeDigest {
    param([string]$Encoded, [string]$Repo)
    if (-not $Encoded) { throw "Launch Template userdata is empty for $Repo." }
    try { $raw = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Encoded)) }
    catch { throw "Launch Template userdata is not valid base64 for $Repo." }
    $prefix = [regex]::Escape("$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$Repo")
    $digests = @([regex]::Matches($raw, "$prefix@(?<digest>sha256:[0-9a-f]{64})", "IgnoreCase") | ForEach-Object { $_.Groups['digest'].Value.ToLowerInvariant() } | Sort-Object -Unique)
    if ($digests.Count -ne 1) { throw "Launch Template must contain exactly one distinct $Repo digest; actual=$($digests -join ',')." }
    return $digests[0]
}

function Save-PinState {
    param($State, [string]$Path)
    if (-not $Path) { return }
    $parent = Split-Path -Parent $Path
    if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    $temp = "$Path.$PID.tmp"
    $State | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $temp -Encoding utf8
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function Wait-PinRefresh {
    param([string]$Asg, [string]$RefreshId)
    for ($elapsed = 0; $elapsed -lt 1800; $elapsed += 15) {
        $result = Invoke-AwsJson @("autoscaling", "describe-instance-refreshes", "--auto-scaling-group-name", $Asg, "--instance-refresh-ids", $RefreshId, "--region", $script:Region, "--output", "json")
        $refresh = @($result.InstanceRefreshes)[0]
        if (-not $refresh) { throw "Refresh disappeared: $RefreshId" }
        if ([string]$refresh.Status -eq "Successful") { return }
        if ([string]$refresh.Status -in @("Failed", "Cancelled", "RollbackFailed", "RollbackSuccessful")) { throw "Refresh $RefreshId ended as $($refresh.Status): $($refresh.StatusReason)" }
        Start-Sleep -Seconds 15
    }
    throw "Refresh $RefreshId timed out."
}

function Get-AsgActualRuntimeDigest {
    param($Asg, [string]$Repo, [string]$Container, [string]$ZeroDesiredDigest)
    $desired = [int]$Asg.DesiredCapacity
    if ($desired -eq 0) { return $ZeroDesiredDigest }
    $instances = @($Asg.Instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" })
    if ($instances.Count -ne $desired) { throw "Pre-pin runtime inventory requires exactly desired=$desired healthy InService instances; actual=$($instances.Count)." }
    $expectedPrefix = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$Repo@"
    $digests = [Collections.Generic.HashSet[string]]::new()
    foreach ($instance in $instances) {
        $remote = "set -e; ID=`$(docker inspect --format '{{.Image}}' '$Container'); docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' `"`$ID`""
        $params = Convert-JsonArgToFileRef (@{commands=@($remote);executionTimeout=@("120")} | ConvertTo-Json -Compress)
        $paramsFile = $params -replace '^file://', ''
        try { $sent = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instance.InstanceId, "--document-name", "AWS-RunShellScript", "--parameters", $params, "--timeout-seconds", "180", "--region", $script:Region, "--output", "json") }
        finally { Remove-TempFiles @($paramsFile) }
        $result = $null
        for ($elapsed = 0; $elapsed -lt 120; $elapsed += 3) {
            Start-Sleep -Seconds 3
            $result = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $sent.Command.CommandId, "--instance-id", $instance.InstanceId, "--region", $script:Region, "--output", "json")
            if ([string]$result.Status -eq "Success") { break }
            if ([string]$result.Status -in @("Failed", "Cancelled", "TimedOut", "Cancelling")) { throw "Pre-pin runtime inventory failed on $($instance.InstanceId): $($result.Status)" }
        }
        $uris = @(([string]$result.StandardOutputContent -split "`r?`n") | Where-Object { $_.StartsWith($expectedPrefix) } | Sort-Object -Unique)
        if ($uris.Count -ne 1 -or $uris[0] -notmatch '@(?<digest>sha256:[0-9a-f]{64})$') { throw "Pre-pin runtime must report exactly one $Repo digest on $($instance.InstanceId)." }
        [void]$digests.Add($matches['digest'].ToLowerInvariant())
    }
    if ($digests.Count -ne 1) { throw "Pre-pin instances disagree on the runtime digest: $(@($digests) -join ',')." }
    return @($digests)[0]
}

function Assert-PinState {
    param($State)
    if ([string]$State.Service -ne $Service -or [string]$State.Repo -ne $deployment.Repo -or [string]$State.ASG -ne $deployment.ASG -or [string]$State.LaunchTemplate -ne $deployment.LaunchTemplate) {
        throw "Pin state does not belong to the requested SSOT service."
    }
    $versions = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $State.LaunchTemplateId, "--versions", '$Latest', "--region", $script:Region, "--output", "json")
    $latest = @($versions.LaunchTemplateVersions)[0]
    $ltDigest = Get-UserDataRuntimeDigest -Encoded ([string]$latest.LaunchTemplateData.UserData) -Repo $deployment.Repo
    if ($ltDigest -ne [string]$State.TargetDigest) { throw "Latest Launch Template digest mismatch: expected=$($State.TargetDigest) actual=$ltDigest" }
    $asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $deployment.ASG, "--region", $script:Region, "--output", "json")
    $asg = @($asgResult.AutoScalingGroups)[0]
    $desired = [int]$asg.DesiredCapacity
    if ($desired -eq 0) {
        Write-Output "VERIFIED_ASG_IMAGE service=$Service desired=0 launchTemplateDigest=$ltDigest"
        return
    }
    $instances = @($asg.Instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" })
    if ($instances.Count -ne $desired) { throw "Runtime verification requires exactly desired=$desired healthy InService instances; actual=$($instances.Count)." }
    $expectedUri = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($deployment.Repo)@$($State.TargetDigest)"
    $container = switch ($Service) { "api" { "academy-api" }; "messaging" { "academy-messaging-worker" }; "ai" { "academy-ai-worker-cpu" }; "tools" { "academy-tools-worker" } }
    foreach ($instance in $instances) {
        $remote = "set -e; ID=`$(docker inspect --format '{{.Image}}' '$container'); docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' `"`$ID`""
        $params = Convert-JsonArgToFileRef (@{commands=@($remote);executionTimeout=@("120")} | ConvertTo-Json -Compress)
        $paramsFile = $params -replace '^file://', ''
        try { $sent = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instance.InstanceId, "--document-name", "AWS-RunShellScript", "--parameters", $params, "--timeout-seconds", "180", "--region", $script:Region, "--output", "json") }
        finally { Remove-TempFiles @($paramsFile) }
        $commandId = [string]$sent.Command.CommandId
        if (-not $commandId) { throw "Runtime verification returned no SSM command id." }
        $result = $null
        for ($elapsed = 0; $elapsed -lt 120; $elapsed += 3) {
            Start-Sleep -Seconds 3
            $result = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $commandId, "--instance-id", $instance.InstanceId, "--region", $script:Region, "--output", "json")
            if ([string]$result.Status -eq "Success") { break }
            if ([string]$result.Status -in @("Failed", "Cancelled", "TimedOut", "Cancelling")) { throw "Runtime verification failed on $($instance.InstanceId): $($result.Status)" }
        }
        $actual = @(([string]$result.StandardOutputContent -split "`r?`n") | Where-Object { $_.Trim() } | Sort-Object -Unique)
        if ($actual.Count -ne 1 -or $actual[0] -ne $expectedUri) { throw "Runtime digest mismatch on $($instance.InstanceId): expected=$expectedUri actual=$($actual -join ',')" }
    }
}

$deployment = switch ($Service) {
    "api" {
        @{
            Repo = $script:EcrApiRepo
            LaunchTemplate = $script:ApiLaunchTemplateName
            ASG = $script:ApiASGName
            UserData = {
                param($ImageUri)
                Get-ApiLaunchTemplateUserData -ApiImageUri $ImageUri -Region $script:Region -SsmApiEnvParam $script:SsmApiEnv -DeploymentId $ImageTag
            }
        }
    }
    "messaging" {
        @{
            Repo = $script:EcrMessagingRepo
            LaunchTemplate = $script:MessagingLaunchTemplateName
            ASG = $script:MessagingASGName
            UserData = {
                param($ImageUri)
                Get-WorkerLaunchTemplateUserData -ImageUri $ImageUri -Region $script:Region -SsmParam $script:SsmWorkersEnv -ContainerName "academy-messaging-worker"
            }
        }
    }
    "ai" {
        @{
            Repo = $script:EcrAiRepo
            LaunchTemplate = $script:AiLaunchTemplateName
            ASG = $script:AiASGName
            UserData = {
                param($ImageUri)
                Get-WorkerLaunchTemplateUserData -ImageUri $ImageUri -Region $script:Region -SsmParam $script:SsmWorkersEnv -ContainerName "academy-ai-worker-cpu"
            }
        }
    }
    "tools" {
        @{
            Repo = $script:EcrToolsRepo
            LaunchTemplate = $script:ToolsLaunchTemplateName
            ASG = $script:ToolsASGName
            UserData = {
                param($ImageUri)
                Get-WorkerLaunchTemplateUserData -ImageUri $ImageUri -Region $script:Region -SsmParam $script:SsmWorkersEnv -ContainerName "academy-tools-worker"
            }
        }
    }
}

if ($VerifyStatePath) {
    if (-not (Test-Path -LiteralPath $VerifyStatePath)) { throw "Pin state not found: $VerifyStatePath" }
    $verifyState = Get-Content -LiteralPath $VerifyStatePath -Raw | ConvertFrom-Json
    Assert-PinState -State $verifyState
    return
}

if ($RestoreStatePath) {
    if (-not (Test-Path -LiteralPath $RestoreStatePath)) { throw "Pin state not found: $RestoreStatePath" }
    $state = Get-Content -LiteralPath $RestoreStatePath -Raw | ConvertFrom-Json
    if ([string]$state.Service -ne $Service -or [string]$state.Repo -ne $deployment.Repo -or [string]$state.ASG -ne $deployment.ASG -or [string]$state.LaunchTemplate -ne $deployment.LaunchTemplate) {
        throw "Refusing to restore state that does not belong to the requested SSOT service."
    }
    $needsVersionRestore = $state.Changed -ne $false
    $refreshes = Invoke-AwsJson @("autoscaling", "describe-instance-refreshes", "--auto-scaling-group-name", $deployment.ASG, "--region", $script:Region, "--output", "json")
    $active = @($refreshes.InstanceRefreshes | Where-Object { $_.Status -in @("Pending", "InProgress", "Cancelling", "RollbackInProgress") })[0]
    if ($active -and [string]$active.Status -notin @("Cancelling", "RollbackInProgress")) {
        Invoke-Aws @("autoscaling", "cancel-instance-refresh", "--auto-scaling-group-name", $deployment.ASG, "--region", $script:Region) -ErrorMessage "cancel failed deployment refresh" | Out-Null
    }
    if ($active) {
        for ($elapsed = 0; $elapsed -lt 900; $elapsed += 10) {
            $observed = Invoke-AwsJson @("autoscaling", "describe-instance-refreshes", "--auto-scaling-group-name", $deployment.ASG, "--instance-refresh-ids", $active.InstanceRefreshId, "--region", $script:Region, "--output", "json")
            $status = [string]@($observed.InstanceRefreshes)[0].Status
            if ($status -in @("Cancelled", "Failed", "RollbackFailed", "RollbackSuccessful", "Successful")) { break }
            Start-Sleep -Seconds 10
        }
        if ($status -notin @("Cancelled", "Failed", "RollbackFailed", "RollbackSuccessful", "Successful")) { throw "Timed out cancelling failed refresh $($active.InstanceRefreshId)." }
    }
    if ($needsVersionRestore) {
        $restoredRaw = Invoke-Aws @(
            "ec2", "create-launch-template-version", "--launch-template-id", $state.LaunchTemplateId,
            "--source-version", [string]$state.PreviousVersion, "--version-description", "compensate $Service $($state.TargetDigest)",
            "--region", $script:Region, "--output", "json"
        ) -ErrorMessage "restore previous $Service Launch Template version"
        $restored = ($restoredRaw | Out-String).Trim() | ConvertFrom-Json
        if (-not $restored.LaunchTemplateVersion.VersionNumber) { throw "Restore returned no Launch Template version." }
    }
    $defaultResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $state.LaunchTemplateId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
    if ([string]@($defaultResult.LaunchTemplateVersions)[0].VersionNumber -ne [string]$state.DefaultVersion) { throw "Launch Template default version changed during pin/restore." }
    $restoreVerify = $state.PSObject.Copy()
    $restoreVerify.TargetDigest = [string]$state.PreviousDigest
    if ($RefreshAndVerify) {
        $asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $deployment.ASG, "--region", $script:Region, "--output", "json")
        if ([int]@($asgResult.AutoScalingGroups)[0].DesiredCapacity -gt 0) {
            $preferences = Convert-JsonArgToFileRef (@{MinHealthyPercentage=100;MaxHealthyPercentage=200;InstanceWarmup=120} | ConvertTo-Json -Compress)
            $preferencesFile = $preferences -replace '^file://', ''
            try { $started = Invoke-AwsJson @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $deployment.ASG, "--preferences", $preferences, "--region", $script:Region, "--output", "json") }
            finally { Remove-TempFiles @($preferencesFile) }
            Wait-PinRefresh -Asg $deployment.ASG -RefreshId ([string]$started.InstanceRefreshId)
        }
    }
    Assert-PinState -State $restoreVerify
    $capacityNow = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $deployment.ASG, "--region", $script:Region, "--output", "json")
    $capacityAsg = @($capacityNow.AutoScalingGroups)[0]
    if (
        [int]$capacityAsg.MinSize -ne [int]$state.PreviousMinSize -or
        [int]$capacityAsg.DesiredCapacity -ne [int]$state.PreviousDesiredCapacity -or
        [int]$capacityAsg.MaxSize -ne [int]$state.PreviousMaxSize
    ) {
        Invoke-Aws @(
            "autoscaling", "update-auto-scaling-group", "--auto-scaling-group-name", $deployment.ASG,
            "--min-size", [string]$state.PreviousMinSize, "--desired-capacity", [string]$state.PreviousDesiredCapacity,
            "--max-size", [string]$state.PreviousMaxSize, "--region", $script:Region
        ) -ErrorMessage "restore previous $Service ASG capacity" | Out-Null
        $capacityReadback = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $deployment.ASG, "--region", $script:Region, "--output", "json")
        $restoredCapacity = @($capacityReadback.AutoScalingGroups)[0]
        if ([int]$restoredCapacity.MinSize -ne [int]$state.PreviousMinSize -or [int]$restoredCapacity.DesiredCapacity -ne [int]$state.PreviousDesiredCapacity -or [int]$restoredCapacity.MaxSize -ne [int]$state.PreviousMaxSize) {
            throw "ASG capacity readback mismatch after compensation."
        }
    }
    Write-Output "RESTORED_ASG_IMAGE service=$Service digest=$($state.PreviousDigest)"
    return
}

if (-not $ImageTag) { throw "ImageTag is required unless VerifyStatePath or RestoreStatePath is used." }

$imageUri = Get-ImmutableEcrImageUri -RepoName $deployment.Repo -ImageTag ($ImageTag.ToLowerInvariant())
Assert-ImmutableEcrImageUri -ImageUri $imageUri
$userDataRaw = & $deployment.UserData $imageUri
if (-not $userDataRaw) { throw "Failed to render $Service Launch Template userdata." }
$userDataB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($userDataRaw))

$ltResult = Invoke-AwsJson @("ec2", "describe-launch-templates", "--launch-template-names", $deployment.LaunchTemplate, "--region", $script:Region, "--output", "json")
$lt = @($ltResult.LaunchTemplates)[0]
if (-not $lt -or -not $lt.LaunchTemplateId) { throw "Launch Template not found: $($deployment.LaunchTemplate)" }

$asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $deployment.ASG, "--region", $script:Region, "--output", "json")
$asg = @($asgResult.AutoScalingGroups)[0]
if (-not $asg) { throw "ASG not found: $($deployment.ASG)" }
if (-not $asg.LaunchTemplate -or $asg.LaunchTemplate.LaunchTemplateId -ne $lt.LaunchTemplateId) {
    throw "ASG $($deployment.ASG) does not use SSOT Launch Template $($deployment.LaunchTemplate). Refusing to mutate it."
}
if ([string]$asg.LaunchTemplate.Version -ne '$Latest') {
    throw "ASG $($deployment.ASG) must track Launch Template version `$Latest; actual=$($asg.LaunchTemplate.Version)."
}

$latestResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $lt.LaunchTemplateId, "--versions", '$Latest', "--region", $script:Region, "--output", "json")
$latest = @($latestResult.LaunchTemplateVersions)[0]
if (-not $latest) { throw "Latest Launch Template version not found: $($deployment.LaunchTemplate)" }
$currentUserData = if ($latest.LaunchTemplateData.UserData) { [string]$latest.LaunchTemplateData.UserData } else { "" }
$defaultResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $lt.LaunchTemplateId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
$defaultVersion = @($defaultResult.LaunchTemplateVersions)[0]
if (-not $defaultVersion) { throw "Default Launch Template version not found: $($deployment.LaunchTemplate)" }
$targetDigest = ($imageUri -split '@')[-1].ToLowerInvariant()
$previousDigest = Get-UserDataRuntimeDigest -Encoded $currentUserData -Repo $deployment.Repo
$containerName = switch ($Service) { "api" { "academy-api" }; "messaging" { "academy-messaging-worker" }; "ai" { "academy-ai-worker-cpu" }; "tools" { "academy-tools-worker" } }
$previousRuntimeDigest = Get-AsgActualRuntimeDigest -Asg $asg -Repo $deployment.Repo -Container $containerName -ZeroDesiredDigest $previousDigest
if ($previousRuntimeDigest -ne $previousDigest) { throw "Refusing to pin over pre-existing LT/runtime drift: lt=$previousDigest runtime=$previousRuntimeDigest" }
$state = [PSCustomObject]@{
    SchemaVersion = 1; Service = $Service; Repo = $deployment.Repo; ASG = $deployment.ASG
    LaunchTemplate = $deployment.LaunchTemplate; LaunchTemplateId = $lt.LaunchTemplateId
    AsgVersionReference = [string]$asg.LaunchTemplate.Version
    DefaultVersion = [string]$defaultVersion.VersionNumber
    PreviousVersion = [string]$latest.VersionNumber
    PreviousDigest = $previousDigest; PreviousRuntimeDigest = $previousRuntimeDigest
    PreviousUserData = $currentUserData; TargetDigest = $targetDigest; TargetImageUri = $imageUri
    PreviousMinSize = [int]$asg.MinSize; PreviousDesiredCapacity = [int]$asg.DesiredCapacity; PreviousMaxSize = [int]$asg.MaxSize
    Changed = ($currentUserData -ne $userDataB64); NewVersion = $null
}
Save-PinState -State $state -Path $StatePath

if ($currentUserData -eq $userDataB64) {
    Write-Output "PINNED_ASG_IMAGE service=$Service image=$imageUri launchTemplateVersion=$($latest.VersionNumber) unchanged=true"
    return
}

$dataRef = Convert-JsonArgToFileRef (@{ UserData = $userDataB64 } | ConvertTo-Json -Compress)
$dataFile = $dataRef -replace '^file://', ''
try {
    $raw = Invoke-Aws @(
        "ec2", "create-launch-template-version",
        "--launch-template-id", $lt.LaunchTemplateId,
        "--source-version", '$Latest',
        "--version-description", "CI $Service $ImageTag",
        "--launch-template-data", $dataRef,
        "--region", $script:Region,
        "--output", "json"
    ) -ErrorMessage "create immutable $Service Launch Template version"
} finally {
    Remove-TempFiles @($dataFile)
}

$created = ($raw | Out-String).Trim() | ConvertFrom-Json
$newVersion = $created.LaunchTemplateVersion.VersionNumber
if (-not $newVersion) { throw "CreateLaunchTemplateVersion returned no version for $Service." }
$state.NewVersion = [string]$newVersion
Save-PinState -State $state -Path $StatePath

$verifiedResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $lt.LaunchTemplateId, "--versions", '$Latest', "--region", $script:Region, "--output", "json")
$verified = @($verifiedResult.LaunchTemplateVersions)[0]
if (-not $verified -or [string]$verified.VersionNumber -ne [string]$newVersion -or [string]$verified.LaunchTemplateData.UserData -ne $userDataB64) {
    throw "Launch Template verification failed for $Service version $newVersion. Instance refresh was not started."
}

Write-Output "PINNED_ASG_IMAGE service=$Service image=$imageUri launchTemplateVersion=$newVersion unchanged=false"
