# One-take fix: Batch RUNNABLE -> reconcile_orphan FAILED and desiredvCpus=0.
# 1) Block root 2) Full diagnosis 3) Disable reconcile (EventBridge) 4) Submit verify job 5) Wait STARTING/RUNNING 6) Report.
# Run from repo root: .\scripts\fix_batch_runnable_orphan_one_take.ps1
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
Set-Location $RepoRoot

$Region = $env:AWS_REGION; if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }; if (-not $Region) { $Region = "ap-northeast-2" }
$QueueName = "academy-video-batch-queue"
$JobDefName = "academy-video-batch-jobdef"

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

# ---------- 1) Caller identity, block root ----------
Write-Host "`n========== 1) Caller identity ==========" -ForegroundColor Cyan
$caller = ExecJson @("sts", "get-caller-identity")
if (-not $caller) {
    Write-Host "FAIL: Could not get caller identity (AWS credentials?)." -ForegroundColor Red
    exit 3
}
$callerArn = $caller.Arn
Write-Host "Arn=$callerArn Account=$($caller.Account)"
if ($callerArn -match ":root\s*$" -or $callerArn -match ":root$") {
    Write-Host "BLOCK: Root credentials. Use IAM user/role. Exit 3." -ForegroundColor Red
    exit 3
}

# ---------- 2) Full diagnosis ----------
Write-Host "`n========== 2) Full diagnosis ==========" -ForegroundColor Cyan
$diagnosePath = Join-Path $ScriptRoot "diagnose_batch_deep.ps1"
if (Test-Path $diagnosePath) {
    & $diagnosePath
    if ($LASTEXITCODE -eq 3) { exit 3 }
} else {
    Write-Host "diagnose_batch_deep.ps1 not found; skipping detailed diagnosis."
}

# ---------- 3) Temporarily disable reconcile ----------
Write-Host "`n========== 3) Disable reconcile (EventBridge rule) ==========" -ForegroundColor Cyan
$rule = ExecJson @("events", "describe-rule", "--name", "academy-reconcile-video-jobs", "--region", $Region)
$fixApplied = "Reconcile rule left ENABLED (rule not found or no access)."
if ($rule) {
    $schedule = $rule.ScheduleExpression
    $desc = $rule.Description
    if (-not $schedule) { $schedule = "rate(5 minutes)" }
    if (-not $desc) { $desc = "Trigger reconcile_batch_video_jobs via Batch" }
    & aws events put-rule --name "academy-reconcile-video-jobs" --state DISABLED --schedule-expression $schedule --description $desc --region $Region 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $fixApplied = "EventBridge rule academy-reconcile-video-jobs set to DISABLED (reconcile paused)."
        Write-Host $fixApplied -ForegroundColor Green
    } else {
        Write-Host "WARN: put-rule DISABLED failed. Reconcile may still run." -ForegroundColor Yellow
    }
} else {
    Write-Host "Rule academy-reconcile-video-jobs not found or no permission." -ForegroundColor Gray
}

# ---------- 4) Latest ACTIVE JobDef revision (numeric sort), submit verify job ----------
Write-Host "`n========== 4) Submit verify job (job_id=test123) ==========" -ForegroundColor Cyan
$jdList = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $JobDefName, "--status", "ACTIVE", "--region", $Region)
if (-not $jdList -or -not $jdList.jobDefinitions -or $jdList.jobDefinitions.Count -eq 0) {
    Write-Host "FAIL: No ACTIVE job definition for $JobDefName" -ForegroundColor Red
    Write-Host "ROOT CAUSE: Reconcile marks DB-unknown RUNNABLE jobs as orphan and terminates with reason reconcile_orphan. Jobs die before CE can scale; desiredvCpus stays 0."
    Write-Host "FIX APPLIED: $fixApplied"
    Write-Host "CURRENT STATUS: No ACTIVE JobDef; submit job failed."
    exit 1
}
$sorted = $jdList.jobDefinitions | Sort-Object -Property { [int]$_.revision } -Descending
$latest = $sorted[0]
$jobDefFull = "$JobDefName`:$($latest.revision)"
Write-Host "Using job definition: $jobDefFull (revision $($latest.revision))"

$sub = ExecJson @("batch", "submit-job", "--job-name", "fix-verify-test123", "--job-queue", $QueueName, "--job-definition", $jobDefFull, "--parameters", "job_id=test123", "--region", $Region)
if (-not $sub -or -not $sub.jobId) {
    Write-Host "FAIL: submit-job failed." -ForegroundColor Red
    Write-Host "ROOT CAUSE: Reconcile terminates RUNNABLE jobs not in DB (reason=reconcile_orphan); CE never scales (desiredvCpus=0)."
    Write-Host "FIX APPLIED: $fixApplied"
    Write-Host "CURRENT STATUS: Submit job failed."
    exit 1
}
$verifyJobId = $sub.jobId
Write-Host "Submitted jobId=$verifyJobId"

# ---------- 5) Wait up to 5 minutes for STARTING or RUNNING ----------
Write-Host "`n========== 5) Wait for STARTING/RUNNING (max 5 min, poll 10s) ==========" -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(5)
$intervalSec = 10
$reached = $false
$finalStatus = $null
$finalReason = $null
while ((Get-Date) -lt $deadline) {
    $jobDesc = ExecJson @("batch", "describe-jobs", "--jobs", $verifyJobId, "--region", $Region)
    if ($jobDesc -and $jobDesc.jobs -and $jobDesc.jobs.Count -gt 0) {
        $j = $jobDesc.jobs[0]
        $st = $j.status
        $finalStatus = $st
        $finalReason = $j.statusReason
        Write-Host "  $st" -NoNewline
        if ($j.statusReason) { Write-Host " statusReason=$($j.statusReason)" } else { Write-Host "" }
        if ($st -eq "STARTING" -or $st -eq "RUNNING") {
            $reached = $true
            break
        }
        if ($st -eq "FAILED" -or $st -eq "SUCCEEDED") {
            break
        }
    }
    Start-Sleep -Seconds $intervalSec
}

# ---------- 6) ROOT CAUSE / FIX APPLIED / CURRENT STATUS ----------
Write-Host "`n========== 6) Report ==========" -ForegroundColor Cyan
Write-Host "ROOT CAUSE: Reconcile (rate 5 min) terminates video-queue jobs that have no DB row (orphan). Test job job_id=test123 has no VideoTranscodeJob row, so it is terminated with reason=reconcile_orphan. Jobs are killed before Batch can scale the CE; desiredvCpus remains 0."
Write-Host "FIX APPLIED: $fixApplied"
if ($reached) {
    Write-Host "CURRENT STATUS: Verify job $verifyJobId reached $finalStatus. Re-enable reconcile when ready: aws events put-rule --name academy-reconcile-video-jobs --state ENABLED --schedule-expression rate(5 minutes) --region $Region"
} else {
    Write-Host "CURRENT STATUS: Verify job $verifyJobId ended as $finalStatus. statusReason=$finalReason"
    Write-Host "Next: Run .\scripts\diagnose_batch_deep.ps1 for CE/ASG/ECS/network details. If still RUNNABLE then FAILED, ensure reconcile stays DISABLED and/or deploy reconcile code with RECONCILE_ORPHAN_MIN_RUNNABLE_MINUTES and RECONCILE_ORPHAN_DISABLED."
}
Write-Host ""
