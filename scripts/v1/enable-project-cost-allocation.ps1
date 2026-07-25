# Enables Project-tagged Academy cost accumulation and backfills current resources.
param(
    [string]$AwsProfile = "default",
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
. (Join-Path $ScriptRoot "resources\cost_tags.ps1")

$script:PlanMode = $Plan
$script:ChangesMade = $false
$null = Load-SSOT -Env "prod"

Write-Host "`n=== Academy Project Cost Allocation (Plan=$Plan) ===" -ForegroundColor Cyan
Ensure-ProjectCostAllocationTags
Write-Host "=== Done ===`n" -ForegroundColor Cyan
