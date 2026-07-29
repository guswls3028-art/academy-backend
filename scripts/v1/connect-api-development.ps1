# Open a local-only SSM tunnel to the active persistent development API.
[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$LocalPort = 18000,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($AwsProfile -and $AwsProfile.Trim()) {
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
Write-Host "Development API tunnel: http://127.0.0.1:$LocalPort -> $instanceId:8000" -ForegroundColor Cyan
& aws ssm start-session `
    --target $instanceId `
    --document-name AWS-StartPortForwardingSession `
    --parameters "portNumber=8000,localPortNumber=$LocalPort" `
    --region $script:Region
if ($LASTEXITCODE -ne 0) {
    throw "SSM port-forwarding session failed."
}
