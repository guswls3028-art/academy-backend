# One-take diagnostic: "worker not starting" (RUNNABLE stuck).
# Run from repo root: .\scripts\diagnose_batch_worker.ps1
# Uses diagnose_batch_worker.py if Python available; else AWS CLI only (limited).
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
Set-Location $RepoRoot

$Region = $env:AWS_REGION; if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }; if (-not $Region) { $Region = "ap-northeast-2" }
$QueueName = "academy-video-batch-queue"
$JobDefName = "academy-video-batch-jobdef"

Write-Host "`n=== Batch Worker Diagnostic (queue=$QueueName region=$Region) ===" -ForegroundColor Cyan

$pythonOk = $false
$py = $null
foreach ($p in @("python", "python3")) {
    try {
        $py = Get-Command $p -ErrorAction SilentlyContinue
        if ($py) { $pythonOk = $true; break }
    } catch {}
}
if ($pythonOk -and $py) {
    Write-Host "Using Python: $($py.Source)" -ForegroundColor Gray
    & $py.Source (Join-Path $ScriptRoot "diagnose_batch_worker.py")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    exit 0
}

Write-Host "Python not found; running AWS CLI-only checks..." -ForegroundColor Yellow
Write-Host ""

# CLI fallback
Write-Host "--- Queue ---" -ForegroundColor Cyan
aws batch describe-job-queues --job-queues $QueueName --region $Region --output table 2>&1
Write-Host "`n--- RUNNABLE jobs ---" -ForegroundColor Cyan
$runnable = aws batch list-jobs --job-queue $QueueName --job-status RUNNABLE --region $Region --output json 2>&1 | ConvertFrom-Json
$list = $runnable.jobSummaryList
if ($list.Count -eq 0) { Write-Host "No RUNNABLE jobs." } else {
    foreach ($j in $list) {
        Write-Host "  jobId=$($j.jobId)"
        $desc = aws batch describe-jobs --jobs $j.jobId --region $Region --query "jobs[0].{status:status,statusReason:statusReason,createdAt:createdAt}" --output json 2>&1 | ConvertFrom-Json
        Write-Host "    status=$($desc.status) statusReason=$($desc.statusReason)"
    }
}
Write-Host "`n--- Compute environments (academy-video) ---" -ForegroundColor Cyan
$ces = aws batch describe-compute-environments --region $Region --query "computeEnvironments[?contains(computeEnvironmentName,'academy-video')].{name:computeEnvironmentName,state:state,status:status,maxvCpus:computeResources.maxvCpus,desiredvCpus:computeResources.desiredvCpus,instanceTypes:computeResources.instanceTypes}" --output table 2>&1
Write-Host $ces
Write-Host "`n--- Job definition (latest ACTIVE) ---" -ForegroundColor Cyan
aws batch describe-job-definitions --job-definition-name $JobDefName --status ACTIVE --region $Region --query "jobDefinitions | sort_by(@, &revision) | [-1].{revision:revision,image:containerProperties.image}" --output table 2>&1

Write-Host "`n========== ROOT CAUSE ==========" -ForegroundColor Yellow
Write-Host "Run with Python (pip install boto3) for full diagnosis: python scripts\diagnose_batch_worker.py"
Write-Host "`n========== FIX PLAN ==========" -ForegroundColor Yellow
Write-Host "  - Ensure CE ENABLED/VALID, maxvCpus >= 1, instanceTypes match image arch (c6g = ARM64)."
Write-Host "  - Push image: .\scripts\build_and_push_ecr_remote.ps1 -VideoWorkerOnly"
Write-Host "  - Run one-take fix: .\scripts\fix_and_redeploy_video_worker.ps1"
Write-Host "`n========== COMMANDS TO APPLY ==========" -ForegroundColor Yellow
Write-Host "  .\scripts\fix_and_redeploy_video_worker.ps1"
Write-Host ""
