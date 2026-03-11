# ==============================================================================
# API ASG instance refresh only (정식 풀배포 중 API만). CI deploy-api-refresh와 동일 동작.
# AWS 프로필: 반드시 default. (-AwsProfile default)
# Usage: pwsh scripts/v1/api-refresh-only.ps1 -AwsProfile default
# ==============================================================================
param([string]$AwsProfile = "default")

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
. (Join-Path $ScriptRoot "core\env.ps1")

if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
    Write-Host "Using AWS_PROFILE: $($env:AWS_PROFILE)" -ForegroundColor Cyan
}

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
$null = Load-SSOT -Env "prod"

$minHealthy = if ($script:ApiInstanceRefreshMinHealthyPercentage -gt 0) { $script:ApiInstanceRefreshMinHealthyPercentage } else { 100 }
$warmup = if ($script:ApiInstanceRefreshInstanceWarmup -gt 0) { $script:ApiInstanceRefreshInstanceWarmup } else { 300 }
$prefs = Convert-JsonArgToFileRef (@{MinHealthyPercentage=$minHealthy;InstanceWarmup=$warmup} | ConvertTo-Json -Compress)

Write-Host "Starting API ASG instance refresh: $($script:ApiASGName) (MinHealthy=$minHealthy%, Warmup=${warmup}s)" -ForegroundColor Cyan
try {
    Invoke-Aws @("autoscaling", "start-instance-refresh", "--auto-scaling-group-name", $script:ApiASGName, "--preferences", $prefs, "--region", $script:Region) -ErrorMessage "start-instance-refresh failed"
    Write-Host "API ASG instance refresh started. New instances will pull academy-api:latest." -ForegroundColor Green
} catch {
    if ($_.Exception.Message -match "InstanceRefreshInProgress") {
        Write-Host "Instance refresh already in progress (idempotent). No new refresh started." -ForegroundColor Green
        exit 0
    }
    throw
}
