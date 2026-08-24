[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TenantCode,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^i-[0-9a-f]+$')]
    [string]$InstanceId,
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
. (Join-Path $ScriptRoot "core\ymath_login_uat.ps1")
Load-SSOT -Env prod | Out-Null

$result = Invoke-AwsJson @(
    "ec2", "describe-instances",
    "--instance-ids", $InstanceId,
    "--region", $script:Region,
    "--output", "json"
)
$instances = @($result.Reservations.Instances | Where-Object { $_.InstanceId })
if ($instances.Count -ne 1 -or [string]$instances[0].InstanceId -ne $InstanceId) {
    throw "Expected the exact runner-owned development instance '$InstanceId'."
}
$tags = @{}
foreach ($tag in @($instances[0].Tags)) { $tags[[string]$tag.Key] = [string]$tag.Value }
if (
    [string]$instances[0].State.Name -ne "running" -or
    $tags["Name"] -ne $script:ApiDevelopmentInstanceName -or
    $tags["ManagedBy"] -ne $script:ApiDevelopmentManagedByTag -or
    $tags["Lifecycle"] -ne "active" -or
    $tags["Environment"] -ne "development"
) {
    throw "Runner-owned instance no longer matches the active persistent-development boundary."
}
$remoteCommand = "/usr/bin/docker exec academy-api python manage.py setup_ymath_realuse_scenario --tenant-code '$TenantCode' --destroy"
$parameters = @{ commands = @($remoteCommand) } | ConvertTo-Json -Compress
$sent = Invoke-AwsJson @(
    "ssm", "send-command",
    "--instance-ids", $InstanceId,
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
        "--instance-id", $InstanceId,
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
if ($null -eq $payload) {
    throw "Persistent-development cleanup did not return a JSON payload."
}
Assert-YmathLoginUatCleanupPayload -Payload $payload -TenantCode $TenantCode | Out-Null

$payload | ConvertTo-Json -Compress
