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

function Wait-AsgRuntimeInventory {
    param([string]$AsgName, [int]$TimeoutSec = 600)
    for ($elapsed = 0; $elapsed -le $TimeoutSec; $elapsed += 15) {
        $result = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $AsgName, "--region", $script:Region, "--output", "json")
        $asg = @($result.AutoScalingGroups)[0]
        if (-not $asg) { throw "ASG not found while waiting for runtime inventory: $AsgName" }
        $desired = [int]$asg.DesiredCapacity
        $instances = @($asg.Instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" })
        if ($instances.Count -eq $desired) {
            return [PSCustomObject]@{ Asg = $asg; Instances = $instances }
        }
        Write-Host "Waiting for ASG runtime inventory to converge: $AsgName healthyInService=$($instances.Count) desired=$desired elapsed=${elapsed}s" -ForegroundColor DarkGray
        if ($elapsed -lt $TimeoutSec) { Start-Sleep -Seconds 15 }
    }
    throw "ASG runtime inventory did not converge within ${TimeoutSec}s: $AsgName"
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
    $inventory = Wait-AsgRuntimeInventory -AsgName $deployment.ASG
    $asg = $inventory.Asg
    if (
        -not $asg.LaunchTemplate -or
        [string]$asg.LaunchTemplate.LaunchTemplateId -ne [string]$State.LaunchTemplateId -or
        [string]$asg.LaunchTemplate.Version -ne '$Latest'
    ) {
        throw "ASG does not track the verified digest-pinned latest Launch Template."
    }
    $desired = [int]$asg.DesiredCapacity
    if ($desired -eq 0) {
        Write-Output "VERIFIED_ASG_IMAGE service=$Service desired=0 launchTemplateDigest=$ltDigest"
        return
    }
    $instances = @($inventory.Instances)
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
        # AWS requires LaunchTemplateData even when SourceVersion supplies the
        # complete baseline. An empty override clones that immutable version.
        $restoreDataRef = Convert-JsonArgToFileRef (@{} | ConvertTo-Json -Compress)
        $restoreDataFile = $restoreDataRef -replace '^file://', ''
        try {
            $restoredRaw = Invoke-Aws @(
                "ec2", "create-launch-template-version", "--launch-template-id", $state.LaunchTemplateId,
                "--source-version", [string]$state.PreviousVersion, "--version-description", "compensate $Service $($state.TargetDigest)",
                "--launch-template-data", $restoreDataRef,
                "--region", $script:Region, "--output", "json"
            ) -ErrorMessage "restore previous $Service Launch Template version"
        } finally {
            Remove-TempFiles @($restoreDataFile)
        }
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
$originalAsgVersionReference = [string]$asg.LaunchTemplate.Version
if ($originalAsgVersionReference -notin @('$Default', '$Latest') -and $originalAsgVersionReference -notmatch '^\d+$') {
    throw "ASG $($deployment.ASG) has an unsupported Launch Template version selector: $originalAsgVersionReference."
}

$activeResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $lt.LaunchTemplateId, "--versions", $originalAsgVersionReference, "--region", $script:Region, "--output", "json")
$activeVersion = @($activeResult.LaunchTemplateVersions)[0]
if (-not $activeVersion) { throw "Active Launch Template version not found: $($deployment.LaunchTemplate) selector=$originalAsgVersionReference" }
$currentUserData = if ($activeVersion.LaunchTemplateData.UserData) { [string]$activeVersion.LaunchTemplateData.UserData } else { "" }
$defaultResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $lt.LaunchTemplateId, "--versions", '$Default', "--region", $script:Region, "--output", "json")
$defaultVersion = @($defaultResult.LaunchTemplateVersions)[0]
if (-not $defaultVersion) { throw "Default Launch Template version not found: $($deployment.LaunchTemplate)" }
$targetDigest = ($imageUri -split '@')[-1].ToLowerInvariant()
$containerName = switch ($Service) { "api" { "academy-api" }; "messaging" { "academy-messaging-worker" }; "ai" { "academy-ai-worker-cpu" }; "tools" { "academy-tools-worker" } }

$declaredDigest = $null
try {
    $declaredDigest = Get-UserDataRuntimeDigest -Encoded $currentUserData -Repo $deployment.Repo
} catch {
    if ($_.Exception.Message -notmatch "must contain exactly one distinct") { throw }
}
$zeroDesiredDigest = if ($declaredDigest) { $declaredDigest } else { $targetDigest }
$previousRuntimeDigest = Get-AsgActualRuntimeDigest -Asg $asg -Repo $deployment.Repo -Container $containerName -ZeroDesiredDigest $zeroDesiredDigest
if ($declaredDigest -and $previousRuntimeDigest -ne $declaredDigest) {
    throw "Refusing to pin over pre-existing LT/runtime drift: lt=$declaredDigest runtime=$previousRuntimeDigest"
}
$previousDigest = $previousRuntimeDigest
$previousImageUri = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($deployment.Repo)@$previousDigest"
$baselineUserDataRaw = & $deployment.UserData $previousImageUri
if (-not $baselineUserDataRaw) { throw "Failed to render $Service baseline Launch Template userdata." }
$baselineUserDataB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($baselineUserDataRaw))
$previousVersion = [string]$activeVersion.VersionNumber
$previousUserData = $currentUserData

# One-time safe cutover from legacy :latest/$Default selectors. The baseline
# points at the exact digest already running, so changing the selector cannot
# change current or future runtime content before the target pin is created.
if ($originalAsgVersionReference -ne '$Latest' -or $currentUserData -ne $baselineUserDataB64) {
    $baselineRef = Convert-JsonArgToFileRef (@{ UserData = $baselineUserDataB64 } | ConvertTo-Json -Compress)
    $baselineFile = $baselineRef -replace '^file://', ''
    try {
        $baselineRaw = Invoke-Aws @(
            "ec2", "create-launch-template-version",
            "--launch-template-id", $lt.LaunchTemplateId,
            "--source-version", [string]$activeVersion.VersionNumber,
            "--version-description", "digest baseline $Service $previousDigest",
            "--launch-template-data", $baselineRef,
            "--region", $script:Region,
            "--output", "json"
        ) -ErrorMessage "create digest-pinned $Service baseline Launch Template version"
    } finally {
        Remove-TempFiles @($baselineFile)
    }
    $baselineCreated = ($baselineRaw | Out-String).Trim() | ConvertFrom-Json
    $baselineVersion = [string]$baselineCreated.LaunchTemplateVersion.VersionNumber
    if (-not $baselineVersion) { throw "Baseline Launch Template creation returned no version for $Service." }

    $launchTemplateRef = Convert-JsonArgToFileRef (@{
        LaunchTemplateId = [string]$lt.LaunchTemplateId
        Version = '$Latest'
    } | ConvertTo-Json -Compress)
    $launchTemplateFile = $launchTemplateRef -replace '^file://', ''
    try {
        Invoke-Aws @(
            "autoscaling", "update-auto-scaling-group",
            "--auto-scaling-group-name", $deployment.ASG,
            "--launch-template", $launchTemplateRef,
            "--region", $script:Region
        ) -ErrorMessage "move $Service ASG to digest-pinned latest Launch Template" | Out-Null
    } finally {
        Remove-TempFiles @($launchTemplateFile)
    }
    $baselineAsgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $deployment.ASG, "--region", $script:Region, "--output", "json")
    $baselineAsg = @($baselineAsgResult.AutoScalingGroups)[0]
    if ([string]$baselineAsg.LaunchTemplate.LaunchTemplateId -ne [string]$lt.LaunchTemplateId -or [string]$baselineAsg.LaunchTemplate.Version -ne '$Latest') {
        throw "ASG selector readback failed after $Service digest baseline cutover."
    }
    $previousVersion = $baselineVersion
    $previousUserData = $baselineUserDataB64
    $currentUserData = $baselineUserDataB64
}

$state = [PSCustomObject]@{
    SchemaVersion = 1; Service = $Service; Repo = $deployment.Repo; ASG = $deployment.ASG
    LaunchTemplate = $deployment.LaunchTemplate; LaunchTemplateId = $lt.LaunchTemplateId
    AsgVersionReference = $originalAsgVersionReference
    DefaultVersion = [string]$defaultVersion.VersionNumber
    PreviousVersion = $previousVersion
    PreviousDigest = $previousDigest; PreviousRuntimeDigest = $previousRuntimeDigest
    PreviousUserData = $previousUserData; TargetDigest = $targetDigest; TargetImageUri = $imageUri
    PreviousMinSize = [int]$asg.MinSize; PreviousDesiredCapacity = [int]$asg.DesiredCapacity; PreviousMaxSize = [int]$asg.MaxSize
    Changed = ($currentUserData -ne $userDataB64); NewVersion = $null
}
Save-PinState -State $state -Path $StatePath

if ($currentUserData -eq $userDataB64) {
    Write-Output "PINNED_ASG_IMAGE service=$Service image=$imageUri launchTemplateVersion=$previousVersion unchanged=true"
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
