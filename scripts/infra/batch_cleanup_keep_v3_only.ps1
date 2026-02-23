# Keep only academy-video-batch-ce-v3. Disable and delete ce, ce-v2. Update queue to v3 only.
# Usage: .\scripts\infra\batch_cleanup_keep_v3_only.ps1 -Region ap-northeast-2
param([string]$Region = "ap-northeast-2")
$ErrorActionPreference = "Stop"
$QueueName = "academy-video-batch-queue"
$KeepCe = "academy-video-batch-ce-v3"
$ToRemove = @("academy-video-batch-ce", "academy-video-batch-ce-v2")

# 1) Ensure no RUNNING jobs
$running = aws batch list-jobs --job-queue $QueueName --job-status RUNNING --region $Region --query "jobSummaryList[].jobId" --output text 2>&1
if ($running -and $running.Trim() -ne "" -and $running -ne "None") {
    Write-Host "FAIL: RUNNING jobs exist. Wait for them to complete: $running" -ForegroundColor Red
    exit 1
}

# 2) Update job queue to only v3
Write-Host "Updating job queue to only $KeepCe..." -ForegroundColor Cyan
aws batch update-job-queue --job-queue $QueueName --region $Region --compute-environment-order "order=1,computeEnvironment=$KeepCe" 2>&1
if ($LASTEXITCODE -ne 0) { Write-Host "update-job-queue failed" -ForegroundColor Red; exit 1 }
Write-Host "Queue updated." -ForegroundColor Green

# 3) Disable then delete old CEs
foreach ($ce in $ToRemove) {
    $exists = aws batch describe-compute-environments --compute-environments $ce --region $Region --query "computeEnvironments[0].status" --output text 2>&1
    if ($exists -eq "None" -or -not $exists) { Write-Host "  $ce not found (skip)" -ForegroundColor Gray; continue }
    Write-Host "Disabling $ce..." -ForegroundColor Yellow
    aws batch update-compute-environment --compute-environment $ce --state DISABLED --region $Region 2>&1 | Out-Null
    Start-Sleep -Seconds 10
    $state = aws batch describe-compute-environments --compute-environments $ce --region $Region --query "computeEnvironments[0].state" --output text 2>&1
    if ($state -eq "DISABLED") {
        Write-Host "Deleting $ce..." -ForegroundColor Yellow
        aws batch delete-compute-environment --compute-environment $ce --region $Region 2>&1
        Write-Host "  $ce delete requested." -ForegroundColor Green
    }
}

# 4) Confirm only v3 ENABLED
Write-Host "`nActive Compute Environment:" -ForegroundColor Cyan
aws batch describe-compute-environments --compute-environments $KeepCe --region $Region --query "computeEnvironments[0].{name:computeEnvironmentName,state:state,status:status}" --output table 2>&1
Write-Host "`nDone. Only $KeepCe should remain active." -ForegroundColor Green
