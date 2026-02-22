# AWS Batch CE: maxvCpus 가 0 또는 2 이면 16 으로 올림 (EC2 못 띄우는 원인 해소)
# Usage: .\scripts\infra\batch_ensure_ce_maxvcpus.ps1 -Region ap-northeast-2
#        .\scripts\infra\batch_ensure_ce_maxvcpus.ps1 -Region ap-northeast-2 -WaitAndCheck  (30초 대기 후 STARTING 작업 확인)

param(
    [string]$Region = "ap-northeast-2",
    [string]$ComputeEnv = "academy-video-batch-ce-v3",
    [string]$JobQueue = "academy-video-batch-queue",
    [int]$NewMaxvCpus = 16,
    [switch]$WaitAndCheck
)

$ErrorActionPreference = "Stop"

Write-Host "[1] Compute environment: $ComputeEnv (vCpus)" -ForegroundColor Cyan
$ce = aws batch describe-compute-environments --compute-environments $ComputeEnv --region $Region --output json 2>&1 | ConvertFrom-Json
if (-not $ce.computeEnvironments -or $ce.computeEnvironments.Count -eq 0) {
    Write-Host "  FAIL: Compute environment not found." -ForegroundColor Red
    exit 1
}
$res = $ce.computeEnvironments[0].computeResources
$min = [int]$res.minvCpus
$desired = [int]$res.desiredvCpus
$max = [int]$res.maxvCpus
Write-Host "  min=$min desired=$desired max=$max" -ForegroundColor Gray

if ($max -eq 0 -or $max -eq 2) {
    Write-Host "[2] maxvCpus is $max -> updating to $NewMaxvCpus..." -ForegroundColor Yellow
    aws batch update-compute-environment --compute-environment $ComputeEnv --compute-resources "maxvCpus=$NewMaxvCpus" --region $Region
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAIL: update-compute-environment failed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK: maxvCpus set to $NewMaxvCpus (CE update may take 30~60s)." -ForegroundColor Green
} else {
    Write-Host "[2] maxvCpus=$max (no change)." -ForegroundColor Green
}

if ($WaitAndCheck) {
    Write-Host "[3] Waiting 30s then checking STARTING jobs..." -ForegroundColor Cyan
    Start-Sleep -Seconds 30
    $starting = aws batch list-jobs --job-queue $JobQueue --job-status STARTING --region $Region --output json 2>&1 | ConvertFrom-Json
    $count = if ($starting.jobSummaryList) { $starting.jobSummaryList.Count } else { 0 }
    Write-Host "  STARTING jobs: $count (if > 0, encoding EC2 is coming up)" -ForegroundColor $(if ($count -gt 0) { "Green" } else { "Gray" })
    if ($starting.jobSummaryList) {
        $starting.jobSummaryList | ForEach-Object { Write-Host "    $($_.jobId) $($_.jobName)" -ForegroundColor Gray }
    }
}

Write-Host ""
Write-Host "Done. To check STARTING jobs later: aws batch list-jobs --job-queue $JobQueue --job-status STARTING --region $Region" -ForegroundColor Gray
