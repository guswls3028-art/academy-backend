# AWS Batch: academy-video-batch-ce-v2 가 ENABLED 인지 확인하고, DISABLED 면 ENABLED 로 변경.
# Job Queue 가 해당 CE 를 참조하는지 확인.
# Usage: .\scripts\infra\batch_ensure_ce_enabled.ps1 -Region ap-northeast-2

param(
    [string]$Region = "ap-northeast-2",
    [string]$ComputeEnv = "academy-video-batch-ce-v2",
    [string]$JobQueue = "academy-video-batch-queue"
)

$ErrorActionPreference = "Stop"

Write-Host "[1] Compute Environment state: $ComputeEnv" -ForegroundColor Cyan
$ce = aws batch describe-compute-environments --compute-environments $ComputeEnv --region $Region --output json 2>&1 | ConvertFrom-Json
if (-not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
    Write-Host "  FAIL: Compute environment not found." -ForegroundColor Red
    exit 1
}
$state = $ce.computeEnvironments[0].state
Write-Host "  state=$state" -ForegroundColor $(if ($state -eq "ENABLED") { "Green" } else { "Yellow" })

if ($state -eq "DISABLED") {
    Write-Host "[2] Updating compute environment to ENABLED..." -ForegroundColor Cyan
    aws batch update-compute-environment --compute-environment $ComputeEnv --state ENABLED --region $Region
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAIL: update-compute-environment failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: state set to ENABLED (may take a moment to become VALID)." -ForegroundColor Green
} else {
    Write-Host "[2] Already ENABLED, skip update." -ForegroundColor Green
}

Write-Host "[3] Job Queue compute environment order: $JobQueue" -ForegroundColor Cyan
$jq = aws batch describe-job-queues --job-queues $JobQueue --region $Region --output json 2>&1 | ConvertFrom-Json
if (-not $jq.jobQueues -or $jq.jobQueues.Count -eq 0) {
    Write-Host "  FAIL: Job queue not found." -ForegroundColor Red
    exit 1
}
$order = $jq.jobQueues[0].computeEnvironmentOrder
$hasV2 = $order | Where-Object { $_.computeEnvironment -like "*$ComputeEnv*" }
if ($hasV2) {
    Write-Host "  OK: Job queue references $ComputeEnv" -ForegroundColor Green
    $order | ForEach-Object { Write-Host "    order=$($_.order) computeEnvironment=$($_.computeEnvironment)" -ForegroundColor Gray }
} else {
    Write-Host "  Job queue does NOT reference $ComputeEnv. Updating job queue..." -ForegroundColor Yellow
    aws batch update-job-queue --job-queue $JobQueue --compute-environment-order "order=1,computeEnvironment=$ComputeEnv" --region $Region
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAIL: update-job-queue failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: Job queue updated to use $ComputeEnv" -ForegroundColor Green
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
