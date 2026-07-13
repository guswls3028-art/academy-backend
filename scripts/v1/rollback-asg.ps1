# Safe ASG runtime rollback. Selects a digest older than the actual Launch
# Template runtime, pins a new LT version, waits for a terminal refresh, and
# proves every healthy container is running the selected digest.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("api", "messaging", "ai", "tools")]
    [string]$Service,
    [string]$ImageTag = "",
    [string]$AwsProfile = "default",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($AwsProfile -and $AwsProfile.Trim()) { $env:AWS_PROFILE = $AwsProfile.Trim() }
$env:AWS_DEFAULT_REGION = "ap-northeast-2"
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\guard.ps1")
. (Join-Path $ScriptRoot "resources\worker_userdata.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")
Load-SSOT -Env prod | Out-Null

# API and messaging persist state-machine values that an older image may not
# understand. A point-in-time DB/queue check is not sufficient because the
# old and new ASG instances overlap during refresh and live traffic can create
# a new-state row after the check. Until images publish a machine-verifiable
# compatibility epoch and the deploy path can quiesce all stateful writers,
# stateful image rollback must fail closed. Rebuild the desired source as a
# new immutable image (roll-forward) instead.
if ($Service -in @("api", "messaging")) {
    throw (
        "STATEFUL_IMAGE_ROLLBACK_BLOCKED service=$Service. " +
        "API/messaging rollback cannot prove schema and state-machine compatibility " +
        "during a live ASG refresh. Roll forward by building the desired source as " +
        "a new immutable release image."
    )
}

$target = switch ($Service) {
    "api" { @{Repo=$script:EcrApiRepo; Asg=$script:ApiASGName; Container="academy-api"} }
    "messaging" { @{Repo=$script:EcrMessagingRepo; Asg=$script:MessagingASGName; Container="academy-messaging-worker"} }
    "ai" { @{Repo=$script:EcrAiRepo; Asg=$script:AiASGName; Container="academy-ai-worker-cpu"} }
    "tools" { @{Repo=$script:EcrToolsRepo; Asg=$script:ToolsASGName; Container="academy-tools-worker"} }
}

function Get-CurrentRuntimeDigest {
    $asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $target.Asg, "--region", $script:Region, "--output", "json")
    $asg = @($asgResult.AutoScalingGroups)[0]
    if (-not $asg) { throw "ASG not found: $($target.Asg)" }
    if (-not $asg.LaunchTemplate -or [string]$asg.LaunchTemplate.Version -ne '$Latest') {
        throw "ASG must use a direct SSOT Launch Template at `$Latest: $($target.Asg)"
    }
    $versionResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $asg.LaunchTemplate.LaunchTemplateId, "--versions", '$Latest', "--region", $script:Region, "--output", "json")
    $version = @($versionResult.LaunchTemplateVersions)[0]
    if (-not $version.LaunchTemplateData.UserData) { throw "Launch Template userdata missing: $($target.Asg)" }
    try {
        $userdata = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$version.LaunchTemplateData.UserData))
    } catch {
        throw "Launch Template userdata is not valid base64: $($target.Asg)"
    }
    $pattern = [regex]::Escape("$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($target.Repo)") + '@(?<digest>sha256:[0-9a-f]{64})'
    $runtimeDigests = @(
        [regex]::Matches($userdata, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase) |
            ForEach-Object { $_.Groups["digest"].Value.ToLowerInvariant() } |
            Sort-Object -Unique
    )
    if ($runtimeDigests.Count -ne 1) {
        throw "Launch Template must contain exactly one distinct $($target.Repo) digest: $($target.Asg) actual=$($runtimeDigests -join ',')"
    }
    return $runtimeDigests[0]
}

function Get-RollbackImage {
    param([string]$CurrentDigest)
    $all = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $target.Repo, "--region", $script:Region, "--output", "json")
    $images = @($all.imageDetails | Where-Object { $_.imageDigest -match '^sha256:[0-9a-f]{64}$' })
    $current = @($images | Where-Object { [string]$_.imageDigest -eq $CurrentDigest })[0]
    if (-not $current) { throw "Current runtime digest is missing from ECR: $($target.Repo)@$CurrentDigest" }
    $currentPushed = [datetimeoffset]$current.imagePushedAt
    $tagPattern = '^sha-(?:[0-9a-f]{8,40}|[0-9a-f]{40}-run-[0-9]+-[0-9]+)$'

    if ($ImageTag) {
        if ($ImageTag -notmatch $tagPattern) { throw "Rollback ImageTag must be an immutable sha-* tag: $ImageTag" }
        $selected = @($images | Where-Object { @($_.imageTags) -contains $ImageTag })[0]
        if (-not $selected) { throw "Rollback tag not found: $($target.Repo):$ImageTag" }
        if ([string]$selected.imageDigest -eq $CurrentDigest) { throw "Rollback target equals the current runtime digest." }
        if ([datetimeoffset]$selected.imagePushedAt -ge $currentPushed) {
            throw "Rollback target must have been pushed before the current runtime: $ImageTag"
        }
        return [PSCustomObject]@{Tag=$ImageTag; Digest=[string]$selected.imageDigest; Pushed=$selected.imagePushedAt}
    }

    $candidates = @($images | ForEach-Object {
        $image = $_
        $tag = @($image.imageTags | Where-Object { $_ -match $tagPattern } | Sort-Object)[0]
        if ($tag -and [string]$image.imageDigest -ne $CurrentDigest -and [datetimeoffset]$image.imagePushedAt -lt $currentPushed) {
            [PSCustomObject]@{Tag=$tag; Digest=[string]$image.imageDigest; Pushed=[datetimeoffset]$image.imagePushedAt}
        }
    } | Where-Object { $_ } | Sort-Object Pushed -Descending)
    if ($candidates.Count -eq 0) { throw "No prior immutable image exists before current runtime $CurrentDigest." }
    return $candidates[0]
}

function Wait-Refresh {
    param([string]$RefreshId)
    for ($elapsed = 0; $elapsed -lt 1800; $elapsed += 15) {
        $result = Invoke-AwsJson @("autoscaling", "describe-instance-refreshes", "--auto-scaling-group-name", $target.Asg, "--instance-refresh-ids", $RefreshId, "--region", $script:Region, "--output", "json")
        $refresh = @($result.InstanceRefreshes)[0]
        if (-not $refresh) { throw "Refresh disappeared: $RefreshId" }
        Write-Host "refresh=$RefreshId status=$($refresh.Status) complete=$($refresh.PercentageComplete)%"
        if ([string]$refresh.Status -eq "Successful") { return }
        if ([string]$refresh.Status -in @("Failed", "Cancelled", "RollbackFailed", "RollbackSuccessful")) {
            throw "Refresh $RefreshId ended as $($refresh.Status): $($refresh.StatusReason)"
        }
        Start-Sleep -Seconds 15
    }
    throw "Refresh $RefreshId timed out after 1800s."
}

function Assert-Runtime {
    param([string]$ExpectedDigest)
    $asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $target.Asg, "--region", $script:Region, "--output", "json")
    $asg = @($asgResult.AutoScalingGroups)[0]
    $ids = @($asg.Instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" } | ForEach-Object { $_.InstanceId })
    if ($ids.Count -lt [int]$asg.DesiredCapacity) { throw "Healthy InService=$($ids.Count) < desired=$($asg.DesiredCapacity) after rollback." }
    $expectedUri = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($target.Repo)@$ExpectedDigest"
    foreach ($id in $ids) {
        $remote = "set -e; IMAGE_ID=`$(docker inspect --format '{{.Image}}' '$($target.Container)'); docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' `"`$IMAGE_ID`""
        $params = Convert-JsonArgToFileRef (@{commands=@($remote);executionTimeout=@("120")} | ConvertTo-Json -Compress)
        $paramsFile = $params -replace '^file://', ''
        try {
            $sent = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $id, "--document-name", "AWS-RunShellScript", "--parameters", $params, "--timeout-seconds", "180", "--region", $script:Region, "--output", "json")
        } finally { Remove-TempFiles @($paramsFile) }
        $commandId = [string]$sent.Command.CommandId
        $result = $null
        for ($elapsed = 0; $elapsed -lt 120; $elapsed += 3) {
            Start-Sleep -Seconds 3
            $result = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $commandId, "--instance-id", $id, "--region", $script:Region, "--output", "json")
            if ([string]$result.Status -eq "Success") { break }
            if ([string]$result.Status -in @("Failed", "Cancelled", "TimedOut", "Cancelling")) { throw "Runtime verification failed on ${id}: $($result.Status) $($result.StandardErrorContent)" }
        }
        if ([string]$result.Status -ne "Success") { throw "Runtime verification timed out on $id." }
        $actual = @([string]$result.StandardOutputContent -split "`r?`n" | Where-Object { $_.Trim() })
        if ($actual -notcontains $expectedUri) { throw "Runtime mismatch on ${id}: expected=$expectedUri actual=$($actual -join ',')" }
    }
    if ($Service -eq "api") {
        foreach ($uri in @("https://api.hakwonplus.com/healthz", "https://api.hakwonplus.com/health")) {
            $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 15
            if ($response.StatusCode -ne 200) { throw "API rollback health failed: $uri status=$($response.StatusCode)" }
        }
    }
}

$currentDigest = Get-CurrentRuntimeDigest
$selected = Get-RollbackImage -CurrentDigest $currentDigest
$selectedUri = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($target.Repo)@$($selected.Digest)"
Write-Host "Current runtime: $($target.Repo)@$currentDigest"
Write-Host "Rollback target: $($selected.Tag) -> $selectedUri"
if ($WhatIf) {
    Write-Host "WhatIf: no Launch Template or ASG mutation was made."
    exit 0
}

$statePath = Join-Path ([IO.Path]::GetTempPath()) "academy-$Service-rollback-$PID.json"
$script:PlanMode = $false
$script:DeployLockAcquired = $false
Acquire-DeployLock -Reg $script:Region
try {
$lockedCurrentDigest = Get-CurrentRuntimeDigest
if ($lockedCurrentDigest -ne $currentDigest) { throw "Runtime changed before rollback lock acquisition: selectedFrom=$currentDigest lockedCurrent=$lockedCurrentDigest" }

$refreshes = Invoke-AwsJson @("autoscaling", "describe-instance-refreshes", "--auto-scaling-group-name", $target.Asg, "--region", $script:Region, "--output", "json")
$activeRefresh = @($refreshes.InstanceRefreshes | Where-Object { $_.Status -in @("Pending", "InProgress", "Cancelling", "RollbackInProgress") })[0]
if ($activeRefresh) { throw "An instance refresh is already active for $($target.Asg): $($activeRefresh.InstanceRefreshId)/$($activeRefresh.Status)" }

& (Join-Path $ScriptRoot "pin-asg-image.ps1") -Service $Service -ImageTag $selected.Tag -StatePath $statePath -AwsProfile $AwsProfile
$asgState = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $target.Asg, "--region", $script:Region, "--output", "json")
$protectedIds = @(@($asgState.AutoScalingGroups)[0].Instances | Where-Object { $_.ProtectedFromScaleIn } | ForEach-Object { $_.InstanceId })
if ($protectedIds.Count -gt 0) {
    $protectArgs = @("autoscaling", "set-instance-protection", "--auto-scaling-group-name", $target.Asg, "--instance-ids") + [string[]]$protectedIds + @("--no-protected-from-scale-in", "--region", $script:Region)
    Invoke-Aws $protectArgs -ErrorMessage "remove scale-in protection before rollback refresh" | Out-Null
}
$preferences = Convert-JsonArgToFileRef (@{MinHealthyPercentage=100;MaxHealthyPercentage=200;InstanceWarmup=120} | ConvertTo-Json -Compress)
$preferencesFile = $preferences -replace '^file://', ''
try {
    $started = Invoke-AwsJson @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $target.Asg, "--preferences", $preferences, "--region", $script:Region, "--output", "json")
} finally { Remove-TempFiles @($preferencesFile) }
$refreshId = [string]$started.InstanceRefreshId
if (-not $refreshId) { throw "start-instance-refresh returned no id." }
Wait-Refresh -RefreshId $refreshId
Assert-Runtime -ExpectedDigest $selected.Digest
Write-Host "ROLLBACK_SUCCESS service=$Service digest=$($selected.Digest) refresh=$refreshId"
} catch {
    $original = $_
    if (Test-Path -LiteralPath $statePath) {
        try { & (Join-Path $ScriptRoot "pin-asg-image.ps1") -Service $Service -RestoreStatePath $statePath -RefreshAndVerify -AwsProfile $AwsProfile }
        catch { throw "Rollback failed and compensation failed: $($_.Exception.Message). Original error: $($original.Exception.Message)" }
    }
    throw $original
} finally {
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    Release-DeployLock -Reg $script:Region
}
