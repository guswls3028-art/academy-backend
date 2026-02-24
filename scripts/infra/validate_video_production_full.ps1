# ==============================================================================
# Full production verification: Batch CE/queue/jobdefs, EventBridge, CloudWatch alarms, SSM env.
# Run from repo root with valid AWS credentials (Region ap-northeast-2, Account 809466760795).
# Usage: .\scripts\infra\validate_video_production_full.ps1 -Region ap-northeast-2
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$ComputeEnvName = "academy-video-batch-ce-v3",
    [string]$JobQueueName = "academy-video-batch-queue",
    [switch]$SkipSsmDump
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)

$fail = 0

# 0. Preflight
Write-Host "`n[0] Preflight: sts get-caller-identity" -ForegroundColor Cyan
$id = aws sts get-caller-identity --region $Region --output json 2>&1 | ConvertFrom-Json
if (-not $id.Account) {
    Write-Host "FAIL: AWS identity check failed." -ForegroundColor Red
    exit 1
}
Write-Host "  Account: $($id.Account) Region: $Region" -ForegroundColor Gray

# 1. Batch CE
Write-Host "`n[1] Batch Compute Environment: $ComputeEnvName" -ForegroundColor Cyan
$ce = aws batch describe-compute-environments --compute-environments $ComputeEnvName --region $Region --output json 2>&1 | ConvertFrom-Json
$ceObj = $ce.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $ComputeEnvName } | Select-Object -First 1
if (-not $ceObj) {
    Write-Host "  FAIL: CE not found." -ForegroundColor Red
    $fail = 1
} elseif ($ceObj.status -ne "VALID" -or $ceObj.state -ne "ENABLED") {
    Write-Host "  FAIL: status=$($ceObj.status) state=$($ceObj.state)" -ForegroundColor Red
    $fail = 1
} else {
    Write-Host "  OK: VALID, ENABLED" -ForegroundColor Green
}

# 2. Job Queue
Write-Host "`n[2] Job Queue: $JobQueueName" -ForegroundColor Cyan
$jq = aws batch describe-job-queues --job-queues $JobQueueName --region $Region --output json 2>&1 | ConvertFrom-Json
$q = $jq.jobQueues | Where-Object { $_.jobQueueName -eq $JobQueueName } | Select-Object -First 1
if (-not $q) {
    Write-Host "  FAIL: Queue not found." -ForegroundColor Red
    $fail = 1
} elseif ($q.state -ne "ENABLED") {
    Write-Host "  FAIL: state=$($q.state)" -ForegroundColor Red
    $fail = 1
} else {
    Write-Host "  OK: ENABLED" -ForegroundColor Green
}

# 3. Job Definitions (worker + ops)
Write-Host "`n[3] Job Definitions (worker, ops-reconcile, ops-scanstuck)" -ForegroundColor Cyan
foreach ($jdName in @("academy-video-batch-jobdef", "academy-video-ops-reconcile", "academy-video-ops-scanstuck")) {
    $jd = aws batch describe-job-definitions --job-definition-name $jdName --status ACTIVE --region $Region --output json 2>&1 | ConvertFrom-Json
    $count = ($jd.jobDefinitions | Where-Object { $_.jobDefinitionName -eq $jdName }).Count
    if ($count -eq 0) {
        Write-Host "  FAIL: $jdName not ACTIVE." -ForegroundColor Red
        $fail = 1
    } else {
        Write-Host "  OK: $jdName" -ForegroundColor Green
    }
}

# 4. EventBridge
Write-Host "`n[4] EventBridge rules + targets" -ForegroundColor Cyan
& (Join-Path $ScriptRoot "validate_video_eventbridge.ps1") -Region $Region -JobQueueName $JobQueueName
if ($LASTEXITCODE -ne 0) { $fail = 1 }

# 5. CloudWatch alarms
Write-Host "`n[5] CloudWatch alarms" -ForegroundColor Cyan
& (Join-Path $ScriptRoot "validate_video_alarms.ps1") -Region $Region
if ($LASTEXITCODE -ne 0) { $fail = 1 }

# 6. SSM dump/verify (optional: avoid if no decrypt permission)
if (-not $SkipSsmDump) {
    Write-Host "`n[6] SSM /academy/workers/env (dump to file, validate keys)" -ForegroundColor Cyan
    & (Join-Path $ScriptRoot "ssm_dump_video_worker_env.ps1") -Region $Region
    if ($LASTEXITCODE -ne 0) { $fail = 1 }
} else {
    Write-Host "`n[6] SSM dump skipped (-SkipSsmDump)" -ForegroundColor Gray
}

if ($fail -ne 0) {
    Write-Host "`nPRODUCTION VERIFICATION: FAIL" -ForegroundColor Red
    exit 1
}
Write-Host "`nPRODUCTION VERIFICATION: PASS" -ForegroundColor Green
exit 0
