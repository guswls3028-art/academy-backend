[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TenantCode,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$TenantCode = $TenantCode.Trim().ToLowerInvariant()
if ($TenantCode -notmatch '^qa-ymath-realuse-[a-z0-9-]+$') {
    throw "TenantCode must be an exact qa-ymath-realuse-* code."
}
if ($AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }

$script:PlanMode = $true
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
Load-SSOT -Env prod | Out-Null

$result = Invoke-AwsJson @(
    "ec2", "describe-instances",
    "--filters",
    "Name=tag:Name,Values=$($script:ApiDevelopmentInstanceName)",
    "Name=tag:ManagedBy,Values=$($script:ApiDevelopmentManagedByTag)",
    "Name=tag:Lifecycle,Values=active",
    "Name=instance-state-name,Values=running",
    "--region", $script:Region,
    "--output", "json"
)
$instances = @($result.Reservations.Instances | Where-Object { $_.InstanceId })
if ($instances.Count -ne 1) {
    throw "Expected exactly one running active API development instance; actual=$($instances.Count)."
}
$instanceId = [string]$instances[0].InstanceId
$remoteCommand = "/usr/bin/docker exec academy-api python manage.py setup_ymath_realuse_scenario --tenant-code '$TenantCode' --destroy"
$parameters = @{ commands = @($remoteCommand) } | ConvertTo-Json -Compress
$sent = Invoke-AwsJson @(
    "ssm", "send-command",
    "--instance-ids", $instanceId,
    "--document-name", "AWS-RunShellScript",
    "--parameters", $parameters,
    "--timeout-seconds", "180",
    "--region", $script:Region,
    "--output", "json"
)
$commandId = [string]$sent.Command.CommandId
if (-not $commandId) { throw "SSM cleanup command returned no command id." }

$invocation = $null
for ($attempt = 0; $attempt -lt 90; $attempt += 1) {
    Start-Sleep -Seconds 2
    $invocation = Invoke-AwsJson @(
        "ssm", "get-command-invocation",
        "--command-id", $commandId,
        "--instance-id", $instanceId,
        "--region", $script:Region,
        "--output", "json"
    )
    if ($invocation.Status -in @("Success", "Failed", "Cancelled", "TimedOut")) { break }
}
if ($null -eq $invocation -or $invocation.Status -ne "Success") {
    throw "Persistent-development cleanup command failed with status '$($invocation.Status)'."
}

$payload = $null
$lines = @([string]$invocation.StandardOutputContent -split "`r?`n")
for ($index = $lines.Count - 1; $index -ge 0; $index -= 1) {
    $candidate = $lines[$index].Trim()
    if (-not $candidate.StartsWith("{")) { continue }
    try {
        $payload = $candidate | ConvertFrom-Json
        break
    } catch {
        continue
    }
}
$validStatus = $payload.status -in @(
    "YMATH_REALUSE_SCENARIO_DESTROYED",
    "YMATH_REALUSE_SCENARIO_ABSENT"
)
if (
    $null -eq $payload -or
    -not $validStatus -or
    [string]$payload.tenant_code -ne $TenantCode -or
    [int]$payload.remaining.tenants -ne 0 -or
    [int]$payload.remaining.users -ne 0
) {
    throw "Persistent-development cleanup did not prove exact tenant/user zero residue."
}

$payload | ConvertTo-Json -Compress
