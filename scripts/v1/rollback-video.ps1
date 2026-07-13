# Safe AWS Batch video rollback. Batch normally scales to zero, so the durable
# runtime proof is the complete set of ACTIVE job definitions plus VALID/ENABLED CEs.
[CmdletBinding()]
param(
    [string]$Sha = "",
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
Load-SSOT -Env prod | Out-Null

$repo = $script:VideoWorkerRepo
$registry = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com"
$jobNames = @($script:SSOT_JobDef | Where-Object { $_ } | Sort-Object -Unique)
if ($jobNames.Count -ne 8) { throw "Video rollback requires exactly eight SSOT job definitions; actual=$($jobNames.Count)" }

$definitions = @{}
$currentDigests = [Collections.Generic.HashSet[string]]::new()
foreach ($name in $jobNames) {
    $result = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $name, "--status", "ACTIVE", "--region", $script:Region, "--output", "json")
    $definition = @($result.jobDefinitions | Sort-Object { [int]$_.revision } -Descending)[0]
    if (-not $definition) { throw "Required ACTIVE Batch job definition not found: $name" }
    $image = [string]$definition.containerProperties.image
    $pattern = '^' + [regex]::Escape("$registry/$repo") + '@(?<digest>sha256:[0-9a-f]{64})$'
    if ($image -notmatch $pattern) { throw "$name is not pinned to the exact video repository digest: $image" }
    [void]$currentDigests.Add($matches["digest"].ToLowerInvariant())
    $definitions[$name] = $definition
}
if ($currentDigests.Count -ne 1) { throw "Video job definitions disagree on current runtime digests: $(@($currentDigests) -join ',')" }
$currentDigest = @($currentDigests)[0]

$all = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $repo, "--region", $script:Region, "--output", "json")
$images = @($all.imageDetails | Where-Object { $_.imageDigest -match '^sha256:[0-9a-f]{64}$' })
$current = @($images | Where-Object { [string]$_.imageDigest -eq $currentDigest })[0]
if (-not $current) { throw "Current video runtime digest is missing from ECR: $currentDigest" }
$currentPushed = [datetimeoffset]$current.imagePushedAt
$tagPattern = '^sha-(?:[0-9a-f]{8,40}|[0-9a-f]{40}-run-[0-9]+-[0-9]+)$'
if ($Sha) {
    if ($Sha -notmatch $tagPattern) { throw "Rollback Sha must be an immutable sha-* tag: $Sha" }
    $selectedImage = @($images | Where-Object { @($_.imageTags) -contains $Sha })[0]
    if (-not $selectedImage) { throw "Video rollback tag not found: $Sha" }
    $selectedTag = $Sha
} else {
    $candidate = @($images | ForEach-Object {
        $image = $_
        $tag = @($image.imageTags | Where-Object { $_ -match $tagPattern } | Sort-Object)[0]
        if ($tag -and [string]$image.imageDigest -ne $currentDigest -and [datetimeoffset]$image.imagePushedAt -lt $currentPushed) {
            [PSCustomObject]@{Image=$image;Tag=$tag;Pushed=[datetimeoffset]$image.imagePushedAt}
        }
    } | Where-Object { $_ } | Sort-Object Pushed -Descending)[0]
    if (-not $candidate) { throw "No prior immutable video image exists before $currentDigest." }
    $selectedImage = $candidate.Image
    $selectedTag = $candidate.Tag
}
$selectedDigest = [string]$selectedImage.imageDigest
if ($selectedDigest -eq $currentDigest) { throw "Video rollback target equals the current runtime digest." }
if ([datetimeoffset]$selectedImage.imagePushedAt -ge $currentPushed) {
    throw "Video rollback target must have been pushed before the current runtime: $selectedTag"
}
$selectedUri = "$registry/$repo@$selectedDigest"
Write-Host "Current video runtime: $currentDigest"
Write-Host "Rollback target: $selectedTag -> $selectedUri"
if ($WhatIf) { Write-Host "WhatIf: no Batch mutation was made."; exit 0 }

$script:PlanMode = $false
$script:DeployLockAcquired = $false
Acquire-DeployLock -Reg $script:Region
try {

$lockedDigests = [Collections.Generic.HashSet[string]]::new()
foreach ($name in $jobNames) {
    $locked = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $name, "--status", "ACTIVE", "--region", $script:Region, "--output", "json")
    $lockedLatest = @($locked.jobDefinitions | Sort-Object { [int]$_.revision } -Descending)[0]
    $lockedImage = [string]$lockedLatest.containerProperties.image
    if ($lockedImage -notmatch ('^' + [regex]::Escape("$registry/$repo") + '@(?<digest>sha256:[0-9a-f]{64})$')) { throw "Video runtime changed to an invalid image before lock acquisition: $name=$lockedImage" }
    [void]$lockedDigests.Add($matches['digest'].ToLowerInvariant())
}
if ($lockedDigests.Count -ne 1 -or @($lockedDigests)[0] -ne $currentDigest) { throw "Video runtime changed before rollback lock acquisition: selectedFrom=$currentDigest locked=$(@($lockedDigests) -join ',')" }

foreach ($ce in @($script:SSOT_CE | Where-Object { $_ })) {
    $result = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ce, "--region", $script:Region, "--output", "json")
    $environment = @($result.computeEnvironments)[0]
    if (-not $environment -or [string]$environment.status -ne "VALID" -or [string]$environment.state -ne "ENABLED") {
        throw "Video compute environment is not VALID/ENABLED before rollback: $ce $($environment.status)/$($environment.state)"
    }
}

function Register-VideoJobDefinitionRevision {
    param([string]$Name, $Definition, [string]$ImageUri)
    $definition = $Definition
    $container = $definition.containerProperties
    $container.image = $ImageUri
    foreach ($property in @("networkInterfaces", "taskArn", "containerInstanceArn", "logStreamName", "assignPublicIp", "networkConfiguration")) {
        $container.PSObject.Properties.Remove($property)
    }
    if (@($container.secrets).Count -eq 0) { $container.PSObject.Properties.Remove("secrets") }
    if ($container.logConfiguration -and @($container.logConfiguration.secretOptions).Count -eq 0) {
        $container.logConfiguration.PSObject.Properties.Remove("secretOptions")
    }
    $containerRef = Convert-JsonArgToFileRef ($container | ConvertTo-Json -Depth 50 -Compress)
    $tempFiles = @($containerRef -replace '^file://', '')
    $args = @("batch", "register-job-definition", "--job-definition-name", $Name, "--type", "container", "--platform-capabilities", "EC2", "--container-properties", $containerRef)
    if ($definition.retryStrategy) {
        $retryRef = Convert-JsonArgToFileRef ($definition.retryStrategy | ConvertTo-Json -Depth 20 -Compress)
        $tempFiles += $retryRef -replace '^file://', ''
        $args += @("--retry-strategy", $retryRef)
    }
    if ($definition.timeout) {
        $timeoutRef = Convert-JsonArgToFileRef ($definition.timeout | ConvertTo-Json -Depth 20 -Compress)
        $tempFiles += $timeoutRef -replace '^file://', ''
        $args += @("--timeout", $timeoutRef)
    }
    foreach ($property in @("parameters", "tags", "consumableResourceProperties")) {
        $value = $definition.$property
        if ($null -ne $value) {
            $valueRef = Convert-JsonArgToFileRef ($value | ConvertTo-Json -Depth 30 -Compress)
            $tempFiles += $valueRef -replace '^file://', ''
            $args += @("--$($property -creplace '([A-Z])', '-$1')".ToLowerInvariant(), $valueRef)
        }
    }
    if ($definition.propagateTags -eq $true) { $args += "--propagate-tags" }
    elseif ($definition.PSObject.Properties.Name -contains "propagateTags") { $args += "--no-propagate-tags" }
    if ($null -ne $definition.schedulingPriority) { $args += @("--scheduling-priority", [string]$definition.schedulingPriority) }
    $args += @("--region", $script:Region, "--output", "json")
    try {
        $registered = Invoke-AwsJson $args
        if (-not $registered.jobDefinitionArn) { throw "register-job-definition returned no ARN: $Name" }
    } finally { Remove-TempFiles $tempFiles }
}

$registeredNames = [Collections.Generic.List[string]]::new()
try {
    foreach ($name in $jobNames) {
        Register-VideoJobDefinitionRevision -Name $name -Definition $definitions[$name] -ImageUri $selectedUri
        $registeredNames.Add($name)
    }
} catch {
    $registrationFailure = $_
    $compensationFailures = @()
    $currentUri = "$registry/$repo@$currentDigest"
    foreach ($name in $registeredNames) {
        try {
            Register-VideoJobDefinitionRevision -Name $name -Definition $definitions[$name] -ImageUri $currentUri
        } catch {
            $compensationFailures += "${name}: $($_.Exception.Message)"
        }
    }
    if ($compensationFailures.Count -gt 0) {
        throw "Video rollback partially registered and compensation failed ($($compensationFailures -join '; ')). Original error: $($registrationFailure.Exception.Message)"
    }
    throw "Video rollback registration failed; completed revisions were compensated back to $currentDigest. Original error: $($registrationFailure.Exception.Message)"
}

foreach ($name in $jobNames) {
    $result = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $name, "--status", "ACTIVE", "--region", $script:Region, "--output", "json")
    $latest = @($result.jobDefinitions | Sort-Object { [int]$_.revision } -Descending)[0]
    if ([string]$latest.containerProperties.image -ne $selectedUri) { throw "Video rollback readback mismatch: $name" }
}
foreach ($ce in @($script:SSOT_CE | Where-Object { $_ })) {
    $result = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ce, "--region", $script:Region, "--output", "json")
    $environment = @($result.computeEnvironments)[0]
    if (-not $environment -or [string]$environment.status -ne "VALID" -or [string]$environment.state -ne "ENABLED") {
        throw "Video compute environment is not VALID/ENABLED: $ce $($environment.status)/$($environment.state)"
    }
}
Write-Host "ROLLBACK_SUCCESS service=video digest=$selectedDigest jobDefinitions=$($jobNames.Count)"
} finally {
    Release-DeployLock -Reg $script:Region
}
