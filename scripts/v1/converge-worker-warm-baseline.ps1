[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ai", "tools")]
    [string]$Service,
    [string]$AwsProfile = "default",
    [switch]$Ci
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if (-not $Ci -and $AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) {
    $env:AWS_DEFAULT_REGION = "ap-northeast-2"
}

. (Join-Path $ScriptRoot "core\env.ps1")
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")

Assert-AwsMutationIdentity | Out-Null
Load-SSOT -Env prod | Out-Null

if ($Service -eq "ai") {
    $asgName = $script:AiASGName
    $expectedMin = [int]$script:AiMinSize
    $expectedDesired = [int]$script:AiDesiredCapacity
    $expectedMax = [int]$script:AiMaxSize
} else {
    $asgName = $script:ToolsASGName
    $expectedMin = [int]$script:ToolsMinSize
    $expectedDesired = [int]$script:ToolsDesiredCapacity
    $expectedMax = [int]$script:ToolsMaxSize
}

if ($expectedMin -lt 1 -or $expectedDesired -lt $expectedMin) {
    throw "$Service warm baseline must have desired >= min >= 1 in params.yaml."
}

$description = Invoke-AwsJson @(
    "autoscaling", "describe-auto-scaling-groups",
    "--auto-scaling-group-names", $asgName,
    "--region", $script:Region,
    "--output", "json"
)
$asg = @($description.AutoScalingGroups)[0]
if (-not $asg -or [string]$asg.AutoScalingGroupName -ne $asgName) {
    throw "ASG not found or name mismatch: $asgName"
}
if ([int]$asg.MaxSize -ne $expectedMax) {
    throw "$asgName max-size drift: expected=$expectedMax actual=$($asg.MaxSize)"
}

# Preserve queue-driven burst capacity. Convergence only raises the floor and
# never scales an active fleet down to the baseline.
$targetDesired = [Math]::Max(
    [int]$asg.DesiredCapacity,
    [Math]::Max($expectedMin, $expectedDesired)
)
if ($targetDesired -gt $expectedMax) {
    throw "$asgName desired capacity exceeds SSOT max: desired=$targetDesired max=$expectedMax"
}

Invoke-Aws @(
    "autoscaling", "update-auto-scaling-group",
    "--auto-scaling-group-name", $asgName,
    "--min-size", [string]$expectedMin,
    "--desired-capacity", [string]$targetDesired,
    "--region", $script:Region
) -ErrorMessage "converge $Service worker warm baseline" | Out-Null

$deadline = (Get-Date).AddMinutes(10)
do {
    $readback = Invoke-AwsJson @(
        "autoscaling", "describe-auto-scaling-groups",
        "--auto-scaling-group-names", $asgName,
        "--region", $script:Region,
        "--output", "json"
    )
    $current = @($readback.AutoScalingGroups)[0]
    $healthyCount = @(
        $current.Instances | Where-Object {
            $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy"
        }
    ).Count
    if (
        [int]$current.MinSize -eq $expectedMin -and
        [int]$current.DesiredCapacity -eq $targetDesired -and
        [int]$current.MaxSize -eq $expectedMax -and
        $healthyCount -ge $expectedMin
    ) {
        Write-Output (
            "WORKER_WARM_BASELINE_READY service=$Service asg=$asgName " +
            "min=$($current.MinSize) desired=$($current.DesiredCapacity) " +
            "max=$($current.MaxSize) healthy=$healthyCount"
        )
        exit 0
    }
    Start-Sleep -Seconds 15
} while ((Get-Date) -lt $deadline)

throw (
    "$asgName warm baseline did not become healthy in 10 minutes: " +
    "min=$($current.MinSize) desired=$($current.DesiredCapacity) " +
    "max=$($current.MaxSize) healthy=$healthyCount"
)
